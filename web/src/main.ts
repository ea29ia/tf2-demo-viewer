import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import './styles.css';
import {
  FLAG_ALIVE,
  FLAG_IN_PVS,
  findFrameIndex,
  parseReplay,
  recordMap,
  samplePoses,
  type PlayerRecord,
  type Pose,
  type Replay,
  type ReplayFrame,
  type ReplayKill,
  type ReplayMeta,
} from './replay';

// ---------------------------------------------------------------------------
// small DOM helpers
// ---------------------------------------------------------------------------

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Не найден DOM-элемент #${id}`);
  return node as T;
}

const viewport = el<HTMLDivElement>('viewport');
const sidebar = el<HTMLElement>('sidebar');
const timeline = el<HTMLInputElement>('timeline');
const timeNow = el<HTMLSpanElement>('timeNow');
const timeTotal = el<HTMLSpanElement>('timeTotal');
const playBtn = el<HTMLButtonElement>('playBtn');
const speedSelect = el<HTMLSelectElement>('speed');
const freeBtn = el<HTMLButtonElement>('freeBtn');
const playersEl = el<HTMLDivElement>('players');
const playerCount = el<HTMLSpanElement>('playerCount');
const killfeedEl = el<HTMLUListElement>('killfeed');
const hideDeadInput = el<HTMLInputElement>('hideDead');
const dimPvsInput = el<HTMLInputElement>('dimPvs');
const mapNameEl = el<HTMLDivElement>('mapName');
const hud = el<HTMLDivElement>('hud');
const overlay = el<HTMLDivElement>('overlay');
const overlayDetails = el<HTMLParagraphElement>('overlayDetails');
const reloadBtn = el<HTMLButtonElement>('reloadBtn');
const dismissBtn = el<HTMLButtonElement>('dismissBtn');
const loadDot = el<HTMLDivElement>('loadDot');

const BASE = import.meta.env.BASE_URL;
const REPLAY_URL = `${BASE}replay/replay.bin`;
const MAP_URL = `${BASE}maps/pl_upward.glb`;

// ---------------------------------------------------------------------------
// TF2 constants
// ---------------------------------------------------------------------------

const TEAM_RED = 2;
const TEAM_BLUE = 3;

const TEAM_COLOR: Record<number, number> = {
  [TEAM_RED]: 0xd94a45,
  [TEAM_BLUE]: 0x4a7fd6,
};

interface ClassInfo {
  abbr: string;
  name: string;
  color: number;
}

const OTHER_CLASS: ClassInfo = { abbr: 'OTH', name: 'Other', color: 0x9aa7b8 };

const CLASSES: Record<number, ClassInfo> = {
  1: { abbr: 'SCO', name: 'Scout', color: 0x8fd14f },
  2: { abbr: 'SOL', name: 'Soldier', color: 0x6f8cff },
  3: { abbr: 'PYR', name: 'Pyro', color: 0xff8b3d },
  4: { abbr: 'DEM', name: 'Demoman', color: 0xc07bd8 },
  5: { abbr: 'HEV', name: 'Heavy', color: 0xe0b04a },
  6: { abbr: 'ENG', name: 'Engineer', color: 0xf2d05a },
  7: { abbr: 'MED', name: 'Medic', color: 0x57d6c4 },
  8: { abbr: 'SNI', name: 'Sniper', color: 0xa7c46a },
  9: { abbr: 'SPY', name: 'Spy', color: 0xd9d9e3 },
};

const CAPSULE_RADIUS = 16;
const CAPSULE_LENGTH = 40; // total height 72 with caps
const CAPSULE_CENTER_Y = CAPSULE_RADIUS + CAPSULE_LENGTH / 2;
const EYE_HEIGHT = 64;

function classInfo(cls: number): ClassInfo {
  return CLASSES[cls] ?? OTHER_CLASS;
}

function teamColor(team: number): number {
  return TEAM_COLOR[team] ?? 0x9aa7b8;
}

function teamClass(team: number): string {
  if (team === TEAM_RED) return 'red';
  if (team === TEAM_BLUE) return 'blue';
  return 'other';
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function fmtTime(sec: number): string {
  const safe = Math.max(0, sec);
  const m = Math.floor(safe / 60);
  const s = safe - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

// ---------------------------------------------------------------------------
// Renderer / scene
// ---------------------------------------------------------------------------

const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(viewport.clientWidth || 1, viewport.clientHeight || 1);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.15;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.domElement.tabIndex = 0;
viewport.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0c141d);
scene.fog = new THREE.Fog(0x101a25, 3500, 19000);

const camera = new THREE.PerspectiveCamera(
  72,
  (viewport.clientWidth || 1) / (viewport.clientHeight || 1),
  4,
  60000,
);
camera.rotation.order = 'YXZ';

const hemi = new THREE.HemisphereLight(0x9fc5ff, 0x2a2118, 2.1);
scene.add(hemi);

const sun = new THREE.DirectionalLight(0xfff0d2, 2.6);
sun.position.set(4200, 7000, 3400);
scene.add(sun);

const fill = new THREE.DirectionalLight(0x6f9cd6, 0.85);
fill.position.set(-5000, 2600, -4200);
scene.add(fill);

const fallbackGrid = new THREE.GridHelper(24000, 48, 0x2c3d52, 0x18232f);
fallbackGrid.position.y = -1;
scene.add(fallbackGrid);

const playerGroup = new THREE.Group();
scene.add(playerGroup);

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

type CameraMode = 'free' | 'follow' | 'pov';

interface NameLabel {
  sprite: THREE.Sprite;
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  texture: THREE.CanvasTexture;
  key: string;
}

interface PlayerView {
  entity: number;
  name: string;
  team: number;
  cls: number;
  health: number;
  flags: number;
  alive: boolean;
  inPvs: boolean;
  seen: boolean;
  group: THREE.Group;
  capsule: THREE.Mesh<THREE.CapsuleGeometry, THREE.MeshStandardMaterial>;
  label: NameLabel;
}

interface LoadedReplay {
  replay: Replay;
  meta: ReplayMeta;
  tickRate: number;
  minTick: number;
  maxTick: number;
  frameMaps: WeakMap<ReplayFrame, Map<number, PlayerRecord>>;
}

let loaded: LoadedReplay | null = null;
const views = new Map<number, PlayerView>();
let metaUsers = new Map<number, string>();
let kills: ReplayKill[] = [];

let playbackTick = 0;
let playing = false;
let speed = 1;
let lastPoses = new Map<number, Pose>();

let selectedEntity: number | null = null;
let cameraMode: CameraMode = 'free';
let hideDead = false;
let dimPvs = true;

let mapBounds: THREE.Box3 | null = null;

// free-fly camera state
let freeYaw = 0;
let freePitch = 0;
let lookDragging = false;
let lastPointerX = 0;
let lastPointerY = 0;
const keys = new Set<string>();

let lastListRender = 0;
let lastListSignature = '';
let lastKillSignature = '';
const killItems: HTMLElement[] = [];

// ---------------------------------------------------------------------------
// Name labels (canvas sprites)
// ---------------------------------------------------------------------------

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function truncate(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let out = text;
  while (out.length > 1 && ctx.measureText(`${out}…`).width > maxWidth) {
    out = out.slice(0, -1);
  }
  return `${out}…`;
}

function createNameLabel(): NameLabel {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D context недоступен');

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;

  const material = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
  });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(168, 42, 1);
  sprite.renderOrder = 1000;

  return { sprite, canvas, ctx, texture, key: '' };
}

function drawNameLabel(label: NameLabel, view: PlayerView): void {
  const key = `${view.name}|${view.team}|${view.cls}|${view.health}|${view.alive}`;
  if (key === label.key) return;
  label.key = key;

  const { ctx, canvas } = label;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const info = classInfo(view.cls);
  const accent = `#${teamColor(view.team).toString(16).padStart(6, '0')}`;

  roundRect(ctx, 6, 10, canvas.width - 12, canvas.height - 20, 18);
  ctx.fillStyle = 'rgba(7, 11, 16, 0.72)';
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.10)';
  ctx.stroke();

  ctx.fillStyle = accent;
  roundRect(ctx, 6, 10, 10, canvas.height - 20, 6);
  ctx.fill();

  ctx.textBaseline = 'middle';
  ctx.font = '600 38px Inter, Arial, sans-serif';
  ctx.fillStyle = view.alive ? '#f2f6fa' : '#8b98a8';
  const name = truncate(ctx, view.name || `#${view.entity}`, 340);
  ctx.fillText(name, 30, 46);

  ctx.font = '700 27px Inter, Arial, sans-serif';
  ctx.fillStyle = `#${info.color.toString(16).padStart(6, '0')}`;
  ctx.fillText(info.abbr, 30, 91);

  ctx.font = '600 27px Inter, Arial, sans-serif';
  ctx.fillStyle = view.alive ? '#d8e2ec' : '#77828f';
  const hp = `${view.health} HP`;
  const hpWidth = ctx.measureText(hp).width;
  ctx.fillText(hp, canvas.width - 28 - hpWidth, 91);

  label.texture.needsUpdate = true;
}

// ---------------------------------------------------------------------------
// Player views
// ---------------------------------------------------------------------------

function createPlayerView(entity: number, name: string, team: number): PlayerView {
  const group = new THREE.Group();
  group.visible = false;

  const material = new THREE.MeshStandardMaterial({
    color: teamColor(team),
    emissive: classInfo(0).color,
    emissiveIntensity: 0.45,
    roughness: 0.62,
    metalness: 0.05,
    transparent: true,
    opacity: 1,
  });

  const geometry = new THREE.CapsuleGeometry(CAPSULE_RADIUS, CAPSULE_LENGTH, 8, 18);
  const capsule = new THREE.Mesh(geometry, material);
  capsule.position.y = CAPSULE_CENTER_Y;
  group.add(capsule);

  const label = createNameLabel();
  label.sprite.position.y = 112;
  group.add(label.sprite);

  playerGroup.add(group);

  const view: PlayerView = {
    entity,
    name,
    team,
    cls: 0,
    health: 0,
    flags: 0,
    alive: true,
    inPvs: true,
    seen: false,
    group,
    capsule,
    label,
  };
  views.set(entity, view);
  return view;
}

function viewFor(entity: number): PlayerView {
  const existing = views.get(entity);
  if (existing) return existing;
  const name = metaUsers.get(entity) ?? `#${entity}`;
  return createPlayerView(entity, name, TEAM_RED);
}

function applyPose(view: PlayerView, pose: Pose): void {
  view.team = pose.team;
  view.cls = pose.cls;
  view.flags = pose.flags;
  view.health = pose.health;
  view.alive = (pose.flags & FLAG_ALIVE) !== 0;
  view.inPvs = (pose.flags & FLAG_IN_PVS) !== 0;
  view.seen = true;

  // Source Z-up -> three.js Y-up: (x, y, z) -> (x, z, -y)
  view.group.position.set(pose.x, pose.z, -pose.y);
  view.group.rotation.y = THREE.MathUtils.degToRad(pose.yaw);

  const mat = view.capsule.material;
  mat.color.setHex(teamColor(pose.team));
  mat.emissive.setHex(classInfo(pose.cls).color);
  mat.emissiveIntensity = view.alive ? 0.5 : 0.08;

  let opacity = 1;
  if (!view.alive) opacity = 0.24;
  if (view.inPvs === false && dimPvs) opacity = Math.min(opacity, 0.35);
  mat.opacity = opacity;
  mat.depthWrite = opacity > 0.7;

  if (view.alive) {
    view.capsule.rotation.z = 0;
    view.capsule.position.y = CAPSULE_CENTER_Y;
  } else {
    view.capsule.rotation.z = Math.PI / 2;
    view.capsule.position.y = CAPSULE_RADIUS;
  }

  view.group.visible = !(hideDead && !view.alive);
  view.label.sprite.visible = view.alive && opacity > 0.4;
  (view.label.sprite.material as THREE.SpriteMaterial).opacity = opacity > 0.5 ? 1 : 0.55;

  drawNameLabel(view.label, view);
}

function hideAllViews(): void {
  for (const view of views.values()) view.group.visible = false;
}

// ---------------------------------------------------------------------------
// Replay playback
// ---------------------------------------------------------------------------

function getFrameMap(frame: ReplayFrame): Map<number, PlayerRecord> {
  if (!loaded) return new Map();
  let map = loaded.frameMaps.get(frame);
  if (!map) {
    map = recordMap(frame);
    loaded.frameMaps.set(frame, map);
  }
  return map;
}

function posesAt(tick: number): Map<number, Pose> {
  if (!loaded || loaded.replay.frames.length === 0) return new Map();
  const frames = loaded.replay.frames;
  const i = findFrameIndex(frames, tick);
  const a = frames[i];
  const b = frames[Math.min(i + 1, frames.length - 1)];
  const mapA = getFrameMap(a);
  const mapB = getFrameMap(b);

  let alpha = 0;
  if (a !== b && b.tick > a.tick) alpha = clamp((tick - a.tick) / (b.tick - a.tick), 0, 1);
  return samplePoses(a, b, mapA, mapB, alpha);
}

function applyReplayPoses(): void {
  if (!loaded) return;
  const poses = posesAt(playbackTick);
  lastPoses = poses;

  for (const pose of poses.values()) {
    const view = viewFor(pose.entity);
    applyPose(view, pose);
  }

  // Hide views that aren't present in the sampled frames.
  for (const view of views.values()) {
    if (!poses.has(view.entity)) view.group.visible = false;
  }

  updateHud();
}

function setupReplay(replay: Replay): void {
  const meta = replay.meta;
  if (replay.frames.length === 0) {
    throw new Error('replay.bin не содержит кадров');
  }

  const tickRate =
    Number.isFinite(meta.tickRate) && meta.tickRate > 0
      ? meta.tickRate
      : meta.intervalPerTick > 0
        ? 1 / meta.intervalPerTick
        : 66.6667;

  const firstTick = replay.frames[0].tick;
  const lastTick = replay.frames[replay.frames.length - 1].tick;
  const minTick = Number.isFinite(meta.startTick)
    ? Math.min(meta.startTick, firstTick)
    : firstTick;
  const maxTick = Math.max(lastTick, minTick + 1);

  loaded = {
    replay,
    meta,
    tickRate,
    minTick,
    maxTick,
    frameMaps: new WeakMap(),
  };

  metaUsers = new Map();
  for (const user of meta.users) {
    metaUsers.set(user.entity, user.name || `#${user.entity}`);
  }
  kills = [...meta.kills].sort((a, b) => a.tick - b.tick);

  // Pre-create side-panel players so the roster is stable.
  for (const user of meta.users) {
    if (views.has(user.entity)) continue;
    const team = user.team === 'red' ? TEAM_RED : user.team === 'blue' ? TEAM_BLUE : 0;
    createPlayerView(user.entity, metaUsers.get(user.entity) ?? '', team);
  }

  mapNameEl.textContent = meta.map || 'unknown map';
  playbackTick = minTick;

  timeline.min = String(minTick);
  timeline.max = String(maxTick);
  timeline.value = String(playbackTick);
  timeline.step = '0.01';

  timeTotal.textContent = fmtTime((maxTick - minTick) / tickRate);
  timeNow.textContent = fmtTime(0);
  applyReplayPoses();
  renderPlayers(true);
  renderKillfeed(true);
  updateTimelineUi();
}

function setTick(tick: number): void {
  if (!loaded) return;
  playbackTick = clamp(tick, loaded.minTick, loaded.maxTick);
  timeline.value = String(playbackTick);
  updateTimelineUi();
  applyReplayPoses();
}

function currentSeconds(): number {
  if (!loaded) return 0;
  return (playbackTick - loaded.minTick) / loaded.tickRate;
}

function updateTimelineUi(): void {
  if (!loaded) {
    timeNow.textContent = '0:00.0';
    timeTotal.textContent = '0:00.0';
    return;
  }
  timeNow.textContent = fmtTime(currentSeconds());
  timeTotal.textContent = fmtTime((loaded.maxTick - loaded.minTick) / loaded.tickRate);
}

function setPlaying(value: boolean): void {
  playing = value;
  playBtn.textContent = playing ? '❚❚' : '▶';
  playBtn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
}

function togglePlay(): void {
  if (!loaded) return;
  if (!playing && playbackTick >= loaded.maxTick - 0.0001) {
    setTick(loaded.minTick);
  }
  setPlaying(!playing);
}

function selectPlayer(entity: number, mode: CameraMode = 'follow'): void {
  selectedEntity = entity;
  cameraMode = mode;
  updateModeButtons();
  renderPlayers(true);
  updateHud();
}

function setMode(mode: CameraMode): void {
  if (mode !== 'free' && selectedEntity === null) {
    // pick the first alive player as a sensible fallback
    let fallback: number | null = null;
    for (const pose of lastPoses.values()) {
      if ((pose.flags & FLAG_ALIVE) !== 0) {
        fallback = pose.entity;
        break;
      }
    }
    if (fallback === null && views.size > 0) fallback = views.keys().next().value ?? null;
    if (fallback === null) return;
    selectedEntity = fallback;
  }
  cameraMode = mode;
  updateModeButtons();
  renderPlayers(true);
  updateHud();
}

function updateModeButtons(): void {
  for (const btn of Array.from(document.querySelectorAll<HTMLButtonElement>('.mode-btn'))) {
    btn.classList.toggle('active', btn.dataset.mode === cameraMode);
  }
}

function updateHud(): void {
  const labels: Record<CameraMode, string> = {
    free: 'Free',
    follow: 'Follow',
    pov: 'POV',
  };
  let text = `Камера: ${labels[cameraMode]}`;
  if (cameraMode !== 'free' && selectedEntity !== null) {
    const view = views.get(selectedEntity);
    text += ` · ${view?.name ?? `#${selectedEntity}`}`;
  }
  if (loaded) {
    text += `  ·  tick ${Math.round(playbackTick)}`;
  }
  hud.textContent = text;
}

// ---------------------------------------------------------------------------
// Side panel
// ---------------------------------------------------------------------------

function renderPlayers(force = false): void {
  if (!loaded) return;
  const now = performance.now();
  if (!force && now - lastListRender < 180) return;

  const rows = Array.from(views.values())
    .filter((v) => v.entity !== 0 && !(hideDead && v.seen && !v.alive))
    .sort((a, b) => {
      const ta = a.team || 9;
      const tb = b.team || 9;
      if (ta !== tb) return ta - tb;
      return (a.name || '').localeCompare(b.name || '', 'ru');
    });

  const signature = rows
    .map(
      (v) =>
        `${v.entity}:${v.team}:${v.cls}:${v.health}:${v.alive ? 1 : 0}:${v.inPvs ? 1 : 0}:${
          v.seen ? 1 : 0
        }:${selectedEntity === v.entity ? 1 : 0}`,
    )
    .join('|');
  if (!force && signature === lastListSignature) {
    lastListRender = now;
    return;
  }
  lastListSignature = signature;
  lastListRender = now;

  playerCount.textContent = String(rows.length);
  playersEl.replaceChildren();

  for (const view of rows) {
    const info = classInfo(view.cls);
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'player-row';
    row.dataset.entity = String(view.entity);
    row.classList.add(`team-${teamClass(view.team)}`);
    if (selectedEntity === view.entity) row.classList.add('selected');
    if (view.seen && !view.alive) row.classList.add('dead');
    if (view.seen && !view.inPvs) row.classList.add('out-pvs');
    row.title = `${view.name} — ${info.name}`;

    const dot = document.createElement('span');
    dot.className = 'p-dot';

    const main = document.createElement('span');
    main.className = 'p-main';
    const name = document.createElement('span');
    name.className = 'p-name';
    name.textContent = view.name || `#${view.entity}`;
    const meta = document.createElement('span');
    meta.className = 'p-meta';
    if (!view.seen) {
      meta.textContent = 'нет в кадре';
    } else {
      const parts = [info.name];
      if (!view.alive) parts.push('мёртв');
      if (!view.inPvs) parts.push('вне PVS');
      meta.textContent = parts.join(' · ');
    }
    main.append(name, meta);

    const hp = document.createElement('span');
    hp.className = 'p-hp';
    hp.textContent = view.seen && view.alive ? String(view.health) : '—';

    row.append(dot, main, hp);
    playersEl.append(row);
  }

  if (rows.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = 'Нет игроков в кадре';
    playersEl.append(empty);
  }
}

function entityName(entity: number | null | undefined): string {
  if (entity === null || entity === undefined || entity === 0) return 'world';
  return metaUsers.get(entity) ?? `#${entity}`;
}

function renderKillfeed(force = false): void {
  if (!loaded) return;
  const tickRate = loaded.tickRate;
  const visible = kills
    .filter((k) => k.tick <= playbackTick && playbackTick - k.tick <= 8 * tickRate)
    .sort((a, b) => b.tick - a.tick)
    .slice(0, 8);

  const signature = visible.map((k) => `${k.tick}:${k.victim}:${k.attacker}`).join('|');
  if (!force && signature === lastKillSignature) {
    // just refresh fade opacity
    for (let i = 0; i < killItems.length; i++) {
      const kill = visible[i];
      if (!kill) continue;
      const age = (playbackTick - kill.tick) / tickRate;
      const opacity = age < 5 ? 1 : Math.max(0, (8 - age) / 3);
      killItems[i].style.opacity = String(opacity);
    }
    return;
  }
  lastKillSignature = signature;
  killfeedEl.replaceChildren();
  killItems.length = 0;

  if (visible.length === 0) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = 'Пока тихо…';
    killfeedEl.append(empty);
    return;
  }

  for (const kill of visible) {
    const age = (playbackTick - kill.tick) / tickRate;
    const opacity = age < 5 ? 1 : Math.max(0, (8 - age) / 3);

    const li = document.createElement('li');
    li.style.opacity = String(opacity);

    const time = document.createElement('span');
    time.className = 'kill-time';
    time.textContent = fmtTime((kill.tick - loaded.minTick) / tickRate);

    const attacker = document.createElement('span');
    attacker.className = 'kill-attacker';
    attacker.textContent = entityName(kill.attacker);

    const victim = document.createElement('span');
    victim.className = 'kill-victim';
    victim.textContent = entityName(kill.victim);

    const weapon = document.createElement('span');
    weapon.className = 'kill-weapon';
    weapon.textContent = kill.weapon || '?';

    li.append(time, attacker, document.createTextNode('✖'), victim, weapon);
    killfeedEl.append(li);
    killItems.push(li);
  }
}

// ---------------------------------------------------------------------------
// Camera
// ---------------------------------------------------------------------------

const tmpVec = new THREE.Vector3();
const tmpTarget = new THREE.Vector3();

function resetFreeCamera(): void {
  let center = new THREE.Vector3(0, 0, 0);
  let radius = 5000;
  if (mapBounds && !mapBounds.isEmpty()) {
    mapBounds.getCenter(center);
    const size = mapBounds.getSize(new THREE.Vector3());
    radius = Math.max(size.x, size.z, 2000) * 0.5;
  }
  camera.position.copy(center).add(new THREE.Vector3(radius * 0.8, radius * 0.95, radius * 1.1));
  camera.lookAt(center);
  camera.rotation.order = 'YXZ';
  freeYaw = camera.rotation.y;
  freePitch = camera.rotation.x;
}

function updateCamera(dt: number): void {
  if (cameraMode === 'free') {
    // WASD + Q/E movement
    const boost = keys.has('ShiftLeft') || keys.has('ShiftRight') ? 3.2 : 1;
    const speedUnits = 850 * boost * dt;
    const forward = tmpVec.set(-Math.sin(freeYaw), 0, -Math.cos(freeYaw));
    const right = tmpTarget.set(Math.cos(freeYaw), 0, -Math.sin(freeYaw));

    if (keys.has('KeyW')) camera.position.addScaledVector(forward, speedUnits);
    if (keys.has('KeyS')) camera.position.addScaledVector(forward, -speedUnits);
    if (keys.has('KeyD')) camera.position.addScaledVector(right, speedUnits);
    if (keys.has('KeyA')) camera.position.addScaledVector(right, -speedUnits);
    if (keys.has('KeyE')) camera.position.y += speedUnits;
    if (keys.has('KeyQ')) camera.position.y -= speedUnits;

    camera.rotation.set(freePitch, freeYaw, 0, 'YXZ');
    fallbackGrid.visible = !mapBounds;
    return;
  }

  fallbackGrid.visible = !mapBounds;
  if (selectedEntity === null) {
    cameraMode = 'free';
    updateModeButtons();
    return;
  }
  const pose = lastPoses.get(selectedEntity);
  if (!pose) return;

  const feet = tmpVec.set(pose.x, pose.z, -pose.y);
  const yawRad = THREE.MathUtils.degToRad(pose.yaw);

  if (cameraMode === 'pov') {
    const pitchRad = THREE.MathUtils.degToRad(pose.pitch);
    const cosP = Math.cos(pitchRad);
    const dir = tmpTarget.set(
      cosP * Math.cos(yawRad),
      -Math.sin(pitchRad),
      -cosP * Math.sin(yawRad),
    );
    camera.position.set(feet.x, feet.y + EYE_HEIGHT, feet.z);
    camera.lookAt(feet.x + dir.x, feet.y + EYE_HEIGHT + dir.y, feet.z + dir.z);
    return;
  }

  // follow from behind
  const forward = tmpTarget.set(Math.cos(yawRad), 0, -Math.sin(yawRad));
  const desired = new THREE.Vector3(
    feet.x - forward.x * 265,
    feet.y + 175,
    feet.z - forward.z * 265,
  );
  const smooth = 1 - Math.exp(-9 * dt);
  camera.position.lerp(desired, smooth);
  camera.lookAt(feet.x, feet.y + 62, feet.z);
}

// ---------------------------------------------------------------------------
// Loaders / error overlay
// ---------------------------------------------------------------------------

function showOverlay(errors: string[]): void {
  overlayDetails.textContent = errors.join('\n');
  overlay.classList.remove('hidden');
}

function hideOverlay(): void {
  overlay.classList.add('hidden');
}

function setLoadState(state: 'ok' | 'err' | 'idle'): void {
  loadDot.classList.toggle('ok', state === 'ok');
  loadDot.classList.toggle('err', state === 'err');
}

async function loadMap(): Promise<void> {
  const loader = new GLTFLoader();
  const gltf = await loader.loadAsync(MAP_URL);

  gltf.scene.rotation.x = -Math.PI / 2; // Source Z-up -> three.js Y-up

  gltf.scene.traverse((obj) => {
    if (!(obj instanceof THREE.Mesh)) return;
    const geometry = obj.geometry;
    const hasColor = Boolean(geometry.getAttribute('color'));
    obj.material = new THREE.MeshStandardMaterial({
      vertexColors: hasColor,
      color: hasColor ? 0xffffff : 0x8d9aab,
      roughness: 0.92,
      metalness: 0.02,
      side: THREE.DoubleSide,
    });
    obj.frustumCulled = true;
  });

  scene.add(gltf.scene);
  gltf.scene.updateMatrixWorld(true);
  mapBounds = new THREE.Box3().setFromObject(gltf.scene);
  fallbackGrid.visible = false;
  resetFreeCamera();
}

async function loadReplay(): Promise<void> {
  const response = await fetch(REPLAY_URL, { cache: 'no-cache' });
  if (!response.ok) {
    throw new Error(`${REPLAY_URL}: HTTP ${response.status} ${response.statusText}`);
  }
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('text/html')) {
    throw new Error(`${REPLAY_URL}: файл не найден (сервер вернул HTML)`);
  }
  const buffer = await response.arrayBuffer();
  const replay = parseReplay(buffer);
  setupReplay(replay);
}

async function boot(): Promise<void> {
  resetFreeCamera();
  updateModeButtons();
  setPlaying(false);
  hideAllViews();

  const [mapResult, replayResult] = await Promise.allSettled([loadMap(), loadReplay()]);
  const errors: string[] = [];

  if (mapResult.status === 'rejected') {
    errors.push(`Карта: ${(mapResult.reason as Error).message}`);
  }
  if (replayResult.status === 'rejected') {
    errors.push(`Реплей: ${(replayResult.reason as Error).message}`);
  }

  if (errors.length > 0) {
    setLoadState('err');
    showOverlay(errors);
  } else {
    setLoadState('ok');
    hideOverlay();
  }
}

// ---------------------------------------------------------------------------
// UI events
// ---------------------------------------------------------------------------

playBtn.addEventListener('click', () => {
  togglePlay();
  renderer.domElement.focus();
});

speedSelect.addEventListener('change', () => {
  speed = Number(speedSelect.value) || 1;
});

timeline.addEventListener('input', () => {
  if (!loaded) return;
  setPlaying(false);
  setTick(Number(timeline.value));
});

freeBtn.addEventListener('click', () => {
  cameraMode = 'free';
  updateModeButtons();
  resetFreeCamera();
  updateHud();
});

for (const btn of Array.from(document.querySelectorAll<HTMLButtonElement>('.mode-btn'))) {
  btn.addEventListener('click', () => {
    setMode((btn.dataset.mode as CameraMode) ?? 'free');
  });
}

playersEl.addEventListener('click', (event) => {
  const target = (event.target as HTMLElement).closest<HTMLElement>('[data-entity]');
  if (!target) return;
  const entity = Number(target.dataset.entity);
  if (!Number.isFinite(entity)) return;
  selectPlayer(entity, 'follow');
});

hideDeadInput.addEventListener('change', () => {
  hideDead = hideDeadInput.checked;
  applyReplayPoses();
  renderPlayers(true);
});

dimPvsInput.addEventListener('change', () => {
  dimPvs = dimPvsInput.checked;
  applyReplayPoses();
});

reloadBtn.addEventListener('click', () => window.location.reload());
dismissBtn.addEventListener('click', () => hideOverlay());

// Mouse look (right mouse button)
renderer.domElement.addEventListener('contextmenu', (event) => event.preventDefault());
renderer.domElement.addEventListener('pointerdown', (event) => {
  if (event.button !== 2) return;
  lookDragging = true;
  lastPointerX = event.clientX;
  lastPointerY = event.clientY;
  renderer.domElement.setPointerCapture(event.pointerId);
});

window.addEventListener('pointermove', (event) => {
  if (!lookDragging) return;
  const dx = event.clientX - lastPointerX;
  const dy = event.clientY - lastPointerY;
  lastPointerX = event.clientX;
  lastPointerY = event.clientY;
  if (cameraMode !== 'free') return;
  freeYaw -= dx * 0.0026;
  freePitch = clamp(freePitch - dy * 0.0026, -1.5, 1.5);
});

window.addEventListener('pointerup', () => {
  lookDragging = false;
});

window.addEventListener('keydown', (event) => {
  const target = event.target as HTMLElement | null;
  const typing =
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement;

  if (event.code === 'Space' && !typing) {
    event.preventDefault();
    togglePlay();
    return;
  }
  if (event.code === 'Escape') {
    cameraMode = 'free';
    updateModeButtons();
    updateHud();
    return;
  }
  if (event.code === 'KeyF' && !typing) {
    setMode('follow');
    return;
  }
  if (event.code === 'KeyV' && !typing) {
    setMode('pov');
    return;
  }

  keys.add(event.code);
});

window.addEventListener('keyup', (event) => {
  keys.delete(event.code);
});

window.addEventListener('blur', () => keys.clear());

// ---------------------------------------------------------------------------
// Animation loop
// ---------------------------------------------------------------------------

const clock = new THREE.Clock();
let lastFrameTime = performance.now();

function resize(): void {
  const w = viewport.clientWidth || 1;
  const h = viewport.clientHeight || 1;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h, false);
}

const resizeObserver = new ResizeObserver(resize);
resizeObserver.observe(viewport);
window.addEventListener('resize', resize);

function animate(): void {
  requestAnimationFrame(animate);

  const now = performance.now();
  const realDt = Math.min(0.1, (now - lastFrameTime) / 1000);
  lastFrameTime = now;
  const dt = clock.getDelta();

  if (loaded && playing) {
    playbackTick += dt * loaded.tickRate * speed;
    if (playbackTick >= loaded.maxTick) {
      playbackTick = loaded.maxTick;
      setPlaying(false);
    }
    timeline.value = String(playbackTick);
    updateTimelineUi();
    applyReplayPoses();
    renderPlayers();
  }

  if (loaded) {
    renderKillfeed();
    // keep HP/team list in sync even while paused
    if (!playing) renderPlayers();
  }

  updateCamera(realDt);
  renderer.render(scene, camera);
}

boot();
animate();

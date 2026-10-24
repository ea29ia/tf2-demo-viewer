// Engineer building rendering (sentry / dispenser / teleporter) from the
// replay v4 per-frame `buildings` block. Data-driven and feature-detected:
// with a v3 replay (frames carry no `buildings` array) every update is a
// no-op, so the build ships safely before the data format lands.
//
// Visual language: readable three.js primitive placeholders only (no new
// deps). Team colors come from tf2.ts. Coordinates use the project-wide
// Source -> scene mapping (x, y, z) -> (x, z, -y); pose yaw converts exactly
// like playerViews.applyPose (group.rotation.y = degToRad(yaw)).

import * as THREE from 'three';
import { findFrameIndex, type BuildingRecord } from '../replay';
import { t } from '../i18n';
import { teamColor, clamp } from './tf2';
import { state } from './state';
import { buildingGroup } from './scene';

// NOTE: BuildingRecord is imported from ../replay (the canonical v4 type).
// The per-frame code still guards with Array.isArray so a v3-shaped frame
// object (no buildings array) is a no-op rather than a crash.

export const BUILDING_SENTRY = 0;
export const BUILDING_DISPENSER = 1;
export const BUILDING_TELE_IN = 2;
export const BUILDING_TELE_OUT = 3;

export const BFLAG_BUILDING = 1 << 0;
export const BFLAG_SAPPED = 1 << 1;
export const BFLAG_MINI = 1 << 2;

const MAX_HEALTH = 255;

interface BuildingView {
  entity: number;
  kind: number;
  group: THREE.Group;
  turret: THREE.Group | null; // sentry head, yaws to the record angle
  spin: THREE.Object3D | null; // teleporter entrance disc
  materials: THREE.MeshStandardMaterial[];
  healthBar: THREE.Sprite;
  barHeight: number; // health bar height above the base
  // last-applied state (to avoid per-frame material churn)
  team: number;
  level: number;
  construction: boolean;
  sapped: boolean;
  mini: boolean;
  lastKey: string;
}

const views = new Map<number, BuildingView>();

// ---------------------------------------------------------------------------
// Shared geometry (created once, reused by every view)
// ---------------------------------------------------------------------------

const geoms: Record<string, THREE.BufferGeometry> = {};

function geom(key: string, make: () => THREE.BufferGeometry): THREE.BufferGeometry {
  if (!geoms[key]) geoms[key] = make();
  return geoms[key];
}

/** Health fraction -> green..yellow..red for the floating bar. */
function healthColor(frac: number, out: THREE.Color): THREE.Color {
  return out.setHSL(clamp(frac, 0, 1) * 0.33, 0.85, 0.5);
}

// ---------------------------------------------------------------------------
// View construction
// ---------------------------------------------------------------------------

function makeMaterial(hex: number): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: hex,
    emissive: hex,
    emissiveIntensity: 0.25,
    roughness: 0.55,
    metalness: 0.1,
  });
}

function buildSentry(view: BuildingView): void {
  const group = view.group;
  const bodyMat = makeMaterial(0xffffff);
  view.materials.push(bodyMat);

  const body = new THREE.Mesh(geom('b-sentry-body', () => new THREE.BoxGeometry(44, 44, 44)), bodyMat);
  body.position.y = 22;
  body.castShadow = true;
  group.add(body);

  // Turret: yaw group + head box + barrel cone pointing local +X (matches the
  // playerViews yaw mapping: rotation.y = degToRad(yaw) turns +X into the
  // Source forward (cos yaw, 0, -sin yaw) in scene space).
  const turret = new THREE.Group();
  turret.position.y = 44;
  const headMat = makeMaterial(0xffffff);
  view.materials.push(headMat);
  const head = new THREE.Mesh(geom('b-sentry-head', () => new THREE.BoxGeometry(34, 22, 30)), headMat);
  head.position.y = 11;
  head.castShadow = true;
  turret.add(head);
  const barrelMat = makeMaterial(0x30363d);
  const barrel = new THREE.Mesh(geom('b-sentry-barrel', () => new THREE.ConeGeometry(5, 20, 8)), barrelMat);
  barrel.rotation.z = -Math.PI / 2; // cone +Y -> +X
  barrel.position.set(26, 11, 0);
  barrel.castShadow = true;
  turret.add(barrel);
  group.add(turret);
  view.turret = turret;
  view.barHeight = 92;
}

function buildDispenser(view: BuildingView): void {
  const group = view.group;
  const baseMat = makeMaterial(0xffffff);
  const topMat = makeMaterial(0xffffff);
  view.materials.push(baseMat, topMat);
  const base = new THREE.Mesh(geom('b-disp-base', () => new THREE.BoxGeometry(52, 56, 52)), baseMat);
  base.position.y = 28;
  base.castShadow = true;
  group.add(base);
  const top = new THREE.Mesh(geom('b-disp-top', () => new THREE.BoxGeometry(38, 18, 38)), topMat);
  top.position.y = 65;
  top.castShadow = true;
  group.add(top);
  view.barHeight = 108;
}

function buildTeleporter(view: BuildingView, exit: boolean): void {
  const group = view.group;
  const padMat = makeMaterial(0xffffff);
  view.materials.push(padMat);
  const pad = new THREE.Mesh(
    geom(exit ? 'b-tele-pad-out' : 'b-tele-pad-in', () => new THREE.CylinderGeometry(30, 32, exit ? 16 : 10, 18)),
    padMat,
  );
  pad.position.y = exit ? 8 : 5;
  pad.castShadow = true;
  group.add(pad);

  const discMat = makeMaterial(0xffffff);
  discMat.emissiveIntensity = 0.8; // the spinning hint glows a bit
  view.materials.push(discMat);
  const disc = new THREE.Mesh(geom('b-tele-disc', () => new THREE.CylinderGeometry(20, 20, 2.5, 18)), discMat);
  disc.position.y = exit ? 17.5 : 11.5;
  group.add(disc);
  // Entrance disc spins (updated per frame); the exit disc stays still.
  view.spin = exit ? null : disc;
  view.barHeight = exit ? 46 : 34;
}

function viewFor(entity: number, kind: number): BuildingView {
  const existing = views.get(entity);
  if (existing && existing.kind === kind) return existing;
  if (existing) clearView(existing); // kind changed (should not happen) — rebuild

  const group = new THREE.Group();
  group.visible = false;
  buildingGroup.add(group);

  const view: BuildingView = {
    entity,
    kind,
    group,
    turret: null,
    spin: null,
    materials: [],
    healthBar: null as unknown as THREE.Sprite,
    barHeight: 60,
    team: 0,
    level: 0,
    construction: false,
    sapped: false,
    mini: false,
    lastKey: '',
  };

  if (kind === BUILDING_SENTRY) buildSentry(view);
  else if (kind === BUILDING_DISPENSER) buildDispenser(view);
  else buildTeleporter(view, kind === BUILDING_TELE_OUT);

  const barMat = new THREE.SpriteMaterial({ transparent: true, depthTest: true, toneMapped: false });
  const bar = new THREE.Sprite(barMat);
  bar.scale.set(40, 5, 1);
  bar.visible = false;
  bar.renderOrder = 900;
  group.add(bar);
  view.healthBar = bar;

  views.set(entity, view);
  return view;
}

function clearView(view: BuildingView): void {
  view.group.removeFromParent();
  for (const mat of view.materials) mat.dispose();
  (view.healthBar.material as THREE.SpriteMaterial).dispose();
}

/** Drop every building view (used when a new demo replaces the current one). */
export function clearBuildings(): void {
  for (const view of views.values()) clearView(view);
  views.clear();
}

// ---------------------------------------------------------------------------
// Per-frame update
// ---------------------------------------------------------------------------

function lerpAngleDeg(a: number, b: number, t: number): number {
  let delta = ((b - a + 540) % 360) - 180;
  if (delta === -180) delta = 180;
  return a + delta * t;
}

/**
 * The contract allows the angle to arrive either as degrees (0..360) or as a
 * raw u16 quantized value (degrees = angle*360/65535). Anything above a
 * plausible degree range is treated as quantized.
 */
function angleDegrees(raw: number): number {
  if (!Number.isFinite(raw)) return 0;
  return raw > 361 ? (raw * 360) / 65535 : raw;
}

function styleKey(team: number, level: number, construction: boolean, sapped: boolean): string {
  return `${team}|${level}|${construction ? 1 : 0}|${sapped ? 1 : 0}`;
}

function applyViewStyle(view: BuildingView, rec: BuildingRecord, construction: boolean, sapped: boolean): void {
  const key = styleKey(rec.team, rec.level, construction, sapped);
  if (key === view.lastKey) return;
  view.lastKey = key;
  view.team = rec.team;
  view.level = rec.level;
  view.construction = construction;
  view.sapped = sapped;
  view.mini = (rec.flags & BFLAG_MINI) !== 0;

  const base = teamColor(rec.team);
  const mini = view.mini;
  // Sentry level scaling (mini sentries are the 60% toy version).
  const scale = mini
    ? 0.6
    : view.kind === BUILDING_SENTRY
      ? [0.9, 1.05, 1.2][clamp(rec.level, 1, 3) - 1] ?? 1
      : 1;
  view.group.scale.setScalar(scale);

  for (const mat of view.materials) {
    mat.transparent = construction;
    mat.opacity = construction ? 0.5 : 1;
    mat.depthWrite = !construction;
    mat.color.setHex(sapped ? 0x9b59d0 : base);
    if (!sapped) mat.emissive.setHex(base);
    mat.needsUpdate = true;
  }
}

function updateView(view: BuildingView, rec: BuildingRecord): void {
  const flags = typeof rec.flags === 'number' ? rec.flags : 0;
  const construction = (flags & BFLAG_BUILDING) !== 0;
  const sapped = (flags & BFLAG_SAPPED) !== 0;
  applyViewStyle(view, rec, construction, sapped);

  // Source Z-up -> scene Y-up: (x, y, z) -> (x, z, -y); group origin sits on
  // the ground (its children are built base-up from y = 0).
  view.group.position.set(rec.x, rec.z, -rec.y);
  view.group.visible = true;

  // Turret yaw (sentry only). Under construction the turret does not track.
  if (view.turret) {
    view.turret.rotation.y = construction ? 0 : THREE.MathUtils.degToRad(angleDegrees(rec.angle));
  }
  // Teleporter entrance spin hint (deterministic, tick-driven).
  if (view.spin) view.spin.rotation.y = state.playbackTick * 0.06;

  // Sapped buildings flicker (cheap emissive pulse); sapped overrides any
  // emissive set by applyViewStyle.
  if (sapped) {
    const pulse = 0.35 + 0.3 * Math.sin(state.playbackTick * 0.4);
    for (const mat of view.materials) {
      mat.emissive.setHex(0x9b59d0);
      mat.emissiveIntensity = pulse;
    }
  } else {
    for (const mat of view.materials) mat.emissiveIntensity = 0.25;
  }

  // Health bar (0..255 fraction of max health), shown when damaged.
  const frac = clamp((typeof rec.health === 'number' ? rec.health : MAX_HEALTH) / MAX_HEALTH, 0, 1);
  const barMat = view.healthBar.material as THREE.SpriteMaterial;
  view.healthBar.visible = frac < 0.995 && !construction;
  if (view.healthBar.visible) {
    healthColor(frac, barMat.color);
    // Local height under the group scale: the bar rides the scaled building.
    view.healthBar.position.y = view.barHeight;
  }
}

function mixBuilding(
  entity: number,
  a: BuildingRecord,
  b: BuildingRecord | undefined,
  t: number,
): void {
  const view = viewFor(entity, a.kind);
  const target = b ?? a;
  const dx = target.x - a.x;
  const dy = target.y - a.y;
  const dz = target.z - a.z;
  const teleported = b !== undefined && Math.hypot(dx, dy, dz) > 512;
  const mix = teleported ? 1 : t;
  const rec: BuildingRecord =
    mix === 0
      ? a
      : {
          ...a,
          x: teleported ? target.x : a.x + (target.x - a.x) * mix,
          y: teleported ? target.y : a.y + (target.y - a.y) * mix,
          z: teleported ? target.z : a.z + (target.z - a.z) * mix,
          angle: teleported
            ? target.angle
            : lerpAngleDeg(angleDegrees(a.angle), angleDegrees(target.angle), mix),
          // Discrete state snaps at the half-way point, like projectiles do.
          team: mix < 0.5 ? a.team : target.team,
          kind: a.kind,
          flags: mix < 0.5 ? a.flags : target.flags,
          level: mix < 0.5 ? a.level : target.level,
          health: mix < 0.5 ? a.health : target.health,
        };
  updateView(view, rec);
}

export function applyBuildings(): void {
  if (!state.loaded) return;
  const frames = state.loaded.replay.frames;
  if (frames.length === 0) return;
  const i = findFrameIndex(frames, state.playbackTick);
  const a = frames[i];
  const b = frames[Math.min(i + 1, frames.length - 1)];
  const listA = a?.buildings;
  const listB = b?.buildings;
  // Feature-detect: a v3 replay has no buildings block at all.
  if (!Array.isArray(listA) && !Array.isArray(listB)) {
    for (const view of views.values()) view.group.visible = false;
    return;
  }

  const alpha =
    a !== b && b.tick > a.tick
      ? clamp((state.playbackTick - a.tick) / (b.tick - a.tick), 0, 1)
      : 0;

  const seen = new Set<number>();
  if (Array.isArray(listA)) {
    for (const pa of listA) {
      if (!pa || typeof pa.entity !== 'number') continue;
      seen.add(pa.entity);
      const pb = Array.isArray(listB) ? listB.find((p) => p?.entity === pa.entity) : undefined;
      mixBuilding(pa.entity, pa, pb, pb ? alpha : 0);
    }
  }
  if (Array.isArray(listB)) {
    for (const pb of listB) {
      if (!pb || typeof pb.entity !== 'number' || seen.has(pb.entity)) continue;
      mixBuilding(pb.entity, pb, pb, 0);
    }
  }

  // Hide views absent from both surrounding frames.
  for (const [id, view] of views) {
    if (!seen.has(id)) view.group.visible = false;
  }
}

// ---------------------------------------------------------------------------
// Click-to-inspect support
// ---------------------------------------------------------------------------

export interface BuildingHit {
  title: string; // localized building name, e.g. "Sentry gun"
  meta: string; // level / health / status summary line
}

function buildingLabel(kind: number, mini: boolean): string {
  if (kind === BUILDING_SENTRY) return mini ? t('buildingMini') : t('buildingSentry');
  if (kind === BUILDING_DISPENSER) return t('buildingDispenser');
  if (kind === BUILDING_TELE_IN) return t('buildingTeleIn');
  return t('buildingTeleOut');
}

/**
 * Ray-pick a building closer than `maxDist` (the best prop hit distance, so
 * buildings win only when they are actually nearer). Returns null otherwise.
 * Invisible views are skipped — three's Raycaster ignores visibility.
 */
export function pickBuilding(raycaster: THREE.Raycaster, maxDist: number): BuildingHit | null {
  const hits = raycaster.intersectObjects(buildingGroup.children, true);
  for (const hit of hits) {
    if (hit.distance >= maxDist) break;
    let node: THREE.Object3D | null = hit.object;
    let hidden = false;
    while (node) {
      if (!node.visible) {
        hidden = true;
        break;
      }
      node = node.parent;
    }
    if (hidden) continue;
    // The hit object belongs to exactly one view group.
    let scope: THREE.Object3D | null = hit.object;
    let view: BuildingView | undefined;
    while (scope && !view) {
      for (const candidate of views.values()) {
        if (candidate.group === scope) {
          view = candidate;
          break;
        }
      }
      scope = scope.parent;
    }
    if (!view) continue;
    const rec = currentRecordOf(view);
    const parts: string[] = [];
    if (view.kind === BUILDING_SENTRY) parts.push(t('buildingLevel', { n: rec?.level ?? 1 }));
    if (typeof rec?.health === 'number') {
      parts.push(`${Math.round((rec.health / MAX_HEALTH) * 100)}% HP`);
    }
    if (rec && (rec.flags & BFLAG_SAPPED) !== 0) parts.push(t('buildingSapped'));
    if (rec && (rec.flags & BFLAG_BUILDING) !== 0) parts.push(t('buildingBuilding'));
    return { title: buildingLabel(view.kind, view.mini), meta: parts.join(' · ') };
  }
  return null;
}

/** Latest applied record snapshot for a view (kept by updateView). */
function currentRecordOf(view: BuildingView): BuildingRecord | null {
  if (!state.loaded) return null;
  const frames = state.loaded.replay.frames;
  const frame = frames[findFrameIndex(frames, state.playbackTick)];
  const list = frame?.buildings;
  if (!Array.isArray(list)) return null;
  return list.find((r) => r?.entity === view.entity) ?? null;
}

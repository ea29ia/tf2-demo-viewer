// Playback, loaders and top-level UI event wiring — extracted from main.ts
// (behavior-preserving port; main.ts remains the source of truth until the
// wiring is switched over).
//
// This module owns: applyReplayPoses / setupReplay / setTick, map + replay
// loading, boot(), the animate() loop, and the UI listeners main.ts registered
// at top level (play/speed/timeline/free/mode/player-list/checkbox/overlay
// buttons). Camera + keyboard/pointer input live in camera.ts / input.ts.

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { applyLang, t } from '../i18n';
import { parseReplay, type Replay } from '../replay';
import {
  timeline,
  timeNow,
  timeTotal,
  playBtn,
  speedSelect,
  freeBtn,
  playersEl,
  hideDeadInput,
  dimPvsInput,
  showModelsInput,
  hideFoliageInput,
  wireframeInput,
  mapNameEl,
  overlay,
  overlayDetails,
  reloadBtn,
  dismissBtn,
  loadDot,
  propBoxesInput,
  demoFileInput,
  demoFileBtn,
} from './dom';
import { clamp, fmtTime, TEAM_RED, TEAM_BLUE } from './tf2';
import { state, BASE, MAP_URL, REPLAY_URL, type CameraMode } from './state';
import { scene, camera, sun, fill, hemi, fallbackGrid, renderer } from './scene';
import { resetFreeCamera, updateCamera } from './camera';
import { views, createPlayerView, viewFor, applyPose, hideAllViews } from './playerViews';
import { posesAt } from './frameCache';
import { applyProjectiles } from './projectiles';
import { updateDamageFx } from './damageFx';
import { collectPropEntries, buildPropBoxOverlay } from './inspect';
import { loadPropModels } from './propModels';
import {
  setPlaying,
  togglePlay,
  selectPlayer,
  setMode,
  renderPlayers,
  renderKillfeed,
  updateHud,
  updateTimelineUi,
  updateModeButtons,
} from './ui';

// ---------------------------------------------------------------------------
// Replay playback
// ---------------------------------------------------------------------------

function applyReplayPoses(): void {
  if (!state.loaded) return;
  const poses = posesAt(state.playbackTick);
  state.lastPoses = poses;

  for (const pose of poses.values()) {
    const view = viewFor(pose.entity);
    applyPose(view, pose);
  }

  // Hide views that aren't present in the sampled frames.
  for (const view of views.values()) {
    if (!poses.has(view.entity)) view.group.visible = false;
  }

  applyProjectiles();
  updateDamageFx();
  updateHud();
}

function setupReplay(replay: Replay): void {
  const meta = replay.meta;
  if (replay.frames.length === 0) {
    throw new Error(t('errNoFrames'));
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

  state.loaded = {
    replay,
    meta,
    tickRate,
    minTick,
    maxTick,
    frameMaps: new WeakMap(),
    projMaps: new WeakMap(),
  };

  state.metaUsers = new Map();
  for (const user of meta.users) {
    state.metaUsers.set(user.entity, user.name || `#${user.entity}`);
  }
  state.kills = [...meta.kills].sort((a, b) => a.tick - b.tick);

  // Pre-create side-panel players so the roster is stable.
  for (const user of meta.users) {
    if (views.has(user.entity)) continue;
    const team = user.team === 'red' ? TEAM_RED : user.team === 'blue' ? TEAM_BLUE : 0;
    createPlayerView(user.entity, state.metaUsers.get(user.entity) ?? '', team);
  }

  mapNameEl.textContent = meta.map || 'unknown map';
  state.playbackTick = minTick;

  timeline.min = String(minTick);
  timeline.max = String(maxTick);
  timeline.value = String(state.playbackTick);
  timeline.step = '0.01';

  timeTotal.textContent = fmtTime((maxTick - minTick) / tickRate);
  timeNow.textContent = fmtTime(0);
  applyReplayPoses();
  renderPlayers(true);
  renderKillfeed(true);
  updateTimelineUi();
}

function setTick(tick: number): void {
  if (!state.loaded) return;
  state.playbackTick = clamp(tick, state.loaded.minTick, state.loaded.maxTick);
  timeline.value = String(state.playbackTick);
  updateTimelineUi();
  applyReplayPoses();
}

/**
 * Play/pause with restart-from-end behavior: pressing play on the final tick
 * rewinds to the start first. Lives here (not in ui.togglePlay) so the
 * restart logic exists exactly once, wired to both the play button and the
 * keyboard/touch hooks.
 */
export function togglePlayOrRestart(): void {
  if (!state.loaded) return;
  if (!state.playing && state.playbackTick >= state.loaded.maxTick - 0.0001) {
    setTick(state.loaded.minTick);
  }
  togglePlay();
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

/**
 * Fallback used only when a GLB mesh arrives without any material. Mirrors the
 * look the viewer used to force on every map mesh (grey-blue untextured
 * brushes, vertex colours when the geometry carries a COLOR_0 attribute).
 */
function createFallbackMapMaterial(hasVertexColors: boolean): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    vertexColors: hasVertexColors,
    color: hasVertexColors ? 0xffffff : 0x8d9aab,
    roughness: 0.92,
    metalness: 0.02,
    flatShading: true,
    side: THREE.DoubleSide,
  });
}

/**
 * Overlay the viewer look on the materials authored by the map exporter
 * (one glTF material per primitive). The materials themselves are kept — they
 * own the per-material factors and textures — only the shading flags are
 * overridden. Accepts a single material or a material array.
 */
function applyMapMaterialLook(material: THREE.Material | THREE.Material[]): void {
  const materials = Array.isArray(material) ? material : [material];
  for (const mat of materials) {
    if (!mat) continue;
    if ('flatShading' in mat) {
      (mat as THREE.MeshStandardMaterial).flatShading = true;
      mat.needsUpdate = true; // flatShading is compiled into the shader
    }
  }
}

/** Shape of the WorldVertexTransition metadata written by add_textures_glb. */
interface SourceBlendInfo {
  shader?: string;
  channel?: string;
  texture2Index?: number;
}

/**
 * Carry over the surface flags that define how a material is drawn. The glTF
 * loader encodes `alphaMode` as `alphaTest` (MASK) or
 * `transparent`/`depthWrite` (BLEND); rebuilding a material without copying
 * them made glass, grates and decals render opaque.
 */
function copySurfaceState(src: THREE.Material, dst: THREE.Material): void {
  dst.name = src.name;
  dst.side = src.side;
  dst.alphaTest = src.alphaTest;
  dst.transparent = src.transparent;
  dst.opacity = src.opacity;
  dst.premultipliedAlpha = src.premultipliedAlpha;
  // MASK and opaque surfaces must write depth; BLEND normally should not.
  // Opaque materials keep the depthWrite value authored by the loader.
  dst.depthWrite = src.alphaTest > 0 ? true : src.transparent ? false : src.depthWrite;
  dst.needsUpdate = true;
}

/**
 * Contribution of the baked lightmap atlas. OFF by default (0.0 — the user's
 * explicit "убираем запеченный свет"): the sun + shadows do all the shading
 * on the world materials. The constant and the attach plumbing stay in place
 * so the human can dial the baked term back in with this single knob.
 */
const LIGHTMAP_INTENSITY = 0.0;

/**
 * Re-apply only the depth policy to an already-existing material.
 */
function refreshDepthPolicy(mat: THREE.Material): void {
  if (mat.alphaTest > 0) mat.depthWrite = true;
  else if (mat.transparent) mat.depthWrite = false;
}

/**
 * Attach the baked lightmap to a lit world material. The baked atlas is OFF by
 * default (LIGHTMAP_INTENSITY = 0.0): the sun + scene lights and their shadows
 * do all the shading. Only materials on meshes that carry uv1 get one (the
 * "no uv1 -> no lightmap" rule); the plumbing stays so the human can re-enable
 * the baked term via the single knob.
 */
function attachLightMap(
  material: THREE.MeshStandardMaterial,
  lightMap: THREE.Texture,
): void {
  material.lightMap = lightMap;
  material.lightMapIntensity = LIGHTMAP_INTENSITY;
  material.needsUpdate = true;
}

/**
 * WorldVertexTransition (Source): the two textures are blended by the
 * geometry's COLOR_0.r. add_textures_glb exposes texture 2 as `emissiveMap`
 * (with an emissive factor of black), so it is moved out of the emission slot
 * into the blend instead of being dropped. The material stays lit
 * (MeshStandardMaterial) like every other world surface.
 */
function buildWorldVertexTransitionMaterial(
  src: THREE.MeshStandardMaterial,
  lightMap: THREE.Texture,
  blendMap: THREE.Texture,
): THREE.MeshStandardMaterial {
  const material = src.clone();
  // Re-apply the surface flags (alpha handling) between the loader material
  // and the rebuilt MeshStandardMaterial.
  copySurfaceState(src, material);
  attachLightMap(material, lightMap);
  // Source uses COLOR_0.r ONLY as the blend weight. three's vertexColors
  // path would multiply the diffuse by rgb, so it must stay off and the
  // attribute/varying are declared by the injected shader instead.
  material.vertexColors = false;
  // texture 2 arrives in the emissive slot; neutralize it there (the blend
  // shader below samples it directly, so the sampler slot is not needed).
  material.emissive = new THREE.Color(0, 0, 0);
  material.emissiveMap = null;
  material.needsUpdate = true;

  // Both textures are sampled with map's UV set (vMapUv). Their own
  // colorSpace drives three's sRGB internal-format decode, so no manual
  // transfer function is needed there.
  material.onBeforeCompile = (shader) => {
    shader.uniforms.blendMap = { value: blendMap };
    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        '#include <common>\nattribute vec3 color;\nvarying float vWvtBlendWeight;',
      )
      .replace(
        '#include <begin_vertex>',
        '#include <begin_vertex>\nvWvtBlendWeight = color.r;',
      );
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        '#include <common>\nuniform sampler2D blendMap;\nvarying float vWvtBlendWeight;',
      )
      .replace(
        '#include <map_fragment>',
        `vec4 wvtFirst = vec4(1.0);
vec4 wvtSecond = vec4(1.0);
#ifdef USE_MAP
  wvtFirst = texture2D(map, vMapUv);
  wvtSecond = texture2D(blendMap, vMapUv);
#endif
float wvtWeight = clamp(vWvtBlendWeight, 0.0, 1.0);
diffuseColor *= mix(wvtFirst, wvtSecond, wvtWeight);`,
      );
  };
  material.customProgramCacheKey = () => 'wvt-world-vertex-transition-standard';
  return material;
}

/** Shape of the $detail metadata written by add_textures_glb. */
interface SourceDetailInfo {
  textureIndex?: number;
  scale?: number | number[];
  blendMode?: number;
  blendFactor?: number;
}

/**
 * Source $detail, blend mode 0 MODULATE: `final = base * detail * 2`. The
 * detail texture is transported in the emissive slot (add_textures_glb keeps
 * emissiveFactor black, so it contributes no emission), but emission itself is
 * not wanted — so the slot is cleared here and the texture is re-sampled in
 * the fragment shader instead:
 *
 *   diffuseColor.rgb = mix(base, base * detail * 2, blendFactor)
 *
 * The base UVs are already in texture-space units, so `detailScale` tiles the
 * detail texture per base texture tile ($detailscale semantics). Only
 * materials that actually carry the detail get the injected shader — every
 * other material keeps the vanilla program. WVT materials are excluded (their
 * emissive slot carries the blend texture, not the detail).
 */
function applySourceDetailModulate(material: THREE.MeshStandardMaterial): void {
  const detail = material.userData?.sourceDetail as SourceDetailInfo | undefined;
  const detailTex = material.emissiveMap;
  if (!detail || !detailTex || material.userData.sourceBlend) return;
  if (detail.blendMode !== 0) return; // the viewer implements MODULATE only

  // $detailscale is a float or an [u, v] pair; anything else falls back to
  // 1x1 and the whole scale is clamped to a sane range.
  let su = 1;
  let sv = 1;
  if (typeof detail.scale === 'number' && Number.isFinite(detail.scale)) {
    su = sv = detail.scale;
  } else if (
    Array.isArray(detail.scale) &&
    detail.scale.length === 2 &&
    detail.scale.every((v) => Number.isFinite(v))
  ) {
    su = detail.scale[0];
    sv = detail.scale[1];
  }
  const clampScale = (v: number) => Math.min(64, Math.max(0.05, v));
  const factor = Math.min(1, Math.max(0, detail.blendFactor ?? 1.0));

  // Emission is not wanted: black emissive, slot freed. The texture object is
  // kept alive by the uniform reference below.
  material.emissive = new THREE.Color(0, 0, 0);
  material.emissiveMap = null;
  material.needsUpdate = true;

  const scale = new THREE.Vector2(clampScale(su), clampScale(sv));
  material.onBeforeCompile = (shader) => {
    shader.uniforms.detailMap = { value: detailTex };
    shader.uniforms.detailScale = { value: scale };
    shader.uniforms.detailFactor = { value: factor };
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        '#include <common>\nuniform sampler2D detailMap;\n'
        + 'uniform vec2 detailScale;\nuniform float detailFactor;',
      )
      .replace(
        '#include <map_fragment>',
        `#include <map_fragment>
#ifdef USE_MAP
  vec4 detailTexel = texture2D(detailMap, vMapUv * detailScale);
  diffuseColor.rgb = mix(
    diffuseColor.rgb,
    diffuseColor.rgb * detailTexel.rgb * 2.0,
    detailFactor);
#endif`,
      );
  };
  material.customProgramCacheKey = () => 'world-detail-modulate';
}

async function loadMap(): Promise<void> {
  const loader = new GLTFLoader();
  const gltf = await loader.loadAsync(MAP_URL);

  // Lightmap atlas sidecar (written by bsp2glb.py next to the GLB). Applied
  // as material.lightMap on the world mesh — glTF has no lightmap slot, so
  // this is wired AFTER loading. Failure to load just means lit shading
  // without the baked detail term.
  let lightMap: THREE.Texture | null = null;
  try {
    lightMap = await new THREE.TextureLoader().loadAsync(
      MAP_URL.replace(/\.glb$/, '_lm.png'));
    lightMap.flipY = false;          // atlas UVs are top-left origin
    lightMap.channel = 1;            // sample from uv1 (TEXCOORD_1), NOT uv
    // The baked bytes are display-referred (authored for gamma output), so
    // mark them sRGB: decode -> linear multiply -> re-encode stays identity.
    lightMap.colorSpace = THREE.SRGBColorSpace;
    // The atlas packs 128 cells with 1px gutters: lower mips would blend
    // neighbouring pages together, which Source never does — sample only mip 0.
    lightMap.generateMipmaps = false;
    lightMap.minFilter = THREE.LinearFilter;
    lightMap.magFilter = THREE.LinearFilter;
    lightMap.anisotropy = 1;
    lightMap.needsUpdate = true;
  } catch {
    console.warn('[map] no lightmap atlas; falling back to lit shading');
  }

  gltf.scene.rotation.x = -Math.PI / 2; // Source Z-up -> three.js Y-up

  gltf.scene.traverse((obj) => {
    if (!(obj instanceof THREE.Mesh)) return;
    const hasColor = Boolean(obj.geometry.getAttribute('color'));
    const material: THREE.Material | THREE.Material[] | undefined = obj.material;
    const missingMaterial =
      !material ||
      (Array.isArray(material) && (material.length === 0 || material.every((m) => !m)));
    if (missingMaterial) {
      obj.material = createFallbackMapMaterial(hasColor);
    } else {
      applyMapMaterialLook(material);
    }
    // Lightmap: ONLY meshes that carry TEXCOORD_1 (the world mesh) can
    // sample the atlas. Props/kits/foliage have no uv1, so they keep plain
    // scene lighting (the "no uv1 -> no lightmap" rule). The baked atlas is
    // off by default (intensity 0); the sun + shadows do the shading.
    const hasUv1 = Boolean(obj.geometry.getAttribute('uv1'));
    if (lightMap && hasUv1) {
      const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
      const converted = materials.map((mat) => {
        if (mat instanceof THREE.MeshStandardMaterial) {
          const blend = mat.userData?.sourceBlend as SourceBlendInfo | undefined;
          if (
            hasColor &&
            blend?.shader === 'WorldVertexTransition' &&
            mat.map &&
            mat.emissiveMap
          ) {
            const wvt = buildWorldVertexTransitionMaterial(
              mat,
              lightMap,
              mat.emissiveMap,
            );
            mat.dispose();
            return wvt;
          }
          // The loader material is kept as-is, so its glTF normalMap (and
          // every other slot) is consumed by MeshStandardMaterial directly.
          attachLightMap(mat, lightMap);
          // $detail: consume the emissive-transported detail texture as a
          // MODULATE term (no-op on materials without extras.sourceDetail).
          applySourceDetailModulate(mat);
          return mat;
        }
        if (mat instanceof THREE.MeshBasicMaterial) {
          // Already unlit (e.g. KHR_materials_unlit). Keep the alpha flags the
          // loader derived from glTF alphaMode; only attach the lightmap.
          mat.lightMap = lightMap;
          mat.lightMapIntensity = LIGHTMAP_INTENSITY;
          refreshDepthPolicy(mat);
          return mat;
        }
        return mat;
      });
      obj.material = Array.isArray(obj.material) ? converted : converted[0];
    }
    obj.castShadow = true;
    obj.receiveShadow = true;
    obj.frustumCulled = true;
  });

  scene.add(gltf.scene);
  state.mapMeshes.length = 0;
  state.foliageMeshes = [];
  gltf.scene.traverse((obj) => {
    if (!(obj instanceof THREE.Mesh)) return;
    if (obj.name.toLowerCase().includes('foliage')) {
      state.foliageMeshes.push(obj);
      // Hidden by default; the checkbox (checked) drives visibility.
      obj.visible = !hideFoliageInput.checked;
    } else {
      state.mapMeshes.push(obj);
    }
  });
  gltf.scene.updateMatrixWorld(true);
  state.mapBounds = new THREE.Box3().setFromObject(gltf.scene);
  fallbackGrid.visible = false;

  // Real prop meshes: props whose model exists in the props manifest have no
  // box geometry in the GLB, so load the exported GLBs and instance them.
  // Fire-and-forget: a missing manifest/model just leaves the boxes in place.
  void loadPropModels(gltf.scene, gltf.scene, (meshes, foliageMeshes) => {
    state.mapMeshes.push(...meshes);
    state.foliageMeshes.push(...foliageMeshes);
    // The checkbox may have been toggled while the models were loading.
    if (wireframeInput.checked) applyWireframe();
  });

  const propCount = collectPropEntries(gltf.scene);
  console.log('[inspect] prop entries loaded:', propCount);

  // Prop bounding-box wireframe overlay (one LineSegments, off by default).
  propBoxOverlay = buildPropBoxOverlay();
  scene.add(propBoxOverlay);
  propBoxOverlay.visible = propBoxesInput.checked;

  // Aim the shadow-casting sun at the map and cover its full bounds with the
  // shadow camera (world is Y-up after the -PI/2 rotation).
  const mapBounds = state.mapBounds;
  if (mapBounds && !mapBounds.isEmpty()) {
    const center = mapBounds.getCenter(new THREE.Vector3());
    const size = mapBounds.getSize(new THREE.Vector3());
    const halfExtent = Math.max(size.x, size.y, size.z) * 0.62 + 400;
    sun.position.copy(center).add(new THREE.Vector3(4200, 7000, 3400));
    sun.target.position.copy(center);
    const cam = sun.shadow.camera;
    cam.left = -halfExtent;
    cam.right = halfExtent;
    cam.top = halfExtent;
    cam.bottom = -halfExtent;
    cam.near = 100;
    cam.far = 30000;
    cam.updateProjectionMatrix();
  }

  // The baked lightmap is off (LIGHTMAP_INTENSITY = 0): the sun + scene
  // lights and shadows do the shading, so keep the full original intensities.
  sun.intensity = 2.6;
  fill.intensity = 0.85;
  hemi.intensity = 2.1;

  resetFreeCamera();
  applyWireframe();
}

async function loadReplay(): Promise<void> {
  const response = await fetch(REPLAY_URL, { cache: 'no-cache' });
  if (!response.ok) {
    throw new Error(`${REPLAY_URL}: HTTP ${response.status} ${response.statusText}`);
  }
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('text/html')) {
    throw new Error(t('errFileNotFound', { url: REPLAY_URL }));
  }
  const buffer = await response.arrayBuffer();
  const replay = parseReplay(buffer);
  setupReplay(replay);
}

/**
 * Open a user-selected .dem file: upload it to the dev-server helper
 * (POST /__dump_demo, see web/vite.config.ts), which runs the local Rust
 * dumper and returns a self-contained replay.bin. Only pl_upward is
 * accepted (the map GLB is hardcoded to upward). On success the previous
 * session is discarded and the new replay takes over playback.
 */
async function loadDemoFile(file: File): Promise<void> {
  setPlaying(false);
  demoFileBtn.classList.add('busy');
  // The data-i18n span keeps the original label; applyLang() restores it.
  const label = demoFileBtn.querySelector('span');
  if (label) label.textContent = t('openDemoBusy');
  try {
    const response = await fetch(`${BASE}__dump_demo`, {
      method: 'POST',
      headers: { 'content-type': 'application/octet-stream' },
      body: await file.arrayBuffer(),
    });
    if (!response.ok) {
      let msg = `HTTP ${response.status}`;
      let map = '';
      try {
        const data = (await response.json()) as { error?: string; map?: string };
        if (data.error) msg = data.error;
        if (data.map) map = data.map;
      } catch {
        // Non-JSON error body: fall back to the HTTP status text.
      }
      throw new Error(map ? t('errOnlyUpward', { map }) : t('errDemoDump', { msg }));
    }
    const buffer = await response.arrayBuffer();
    const replay = parseReplay(buffer);
    const map = (replay.meta.map ?? '').toLowerCase();
    if (!map.includes('pl_upward')) {
      throw new Error(t('errOnlyUpward', { map: replay.meta.map ?? '?' }));
    }

    // Reset the previous session: player views (groups, materials, label
    // textures), selection, camera mode. The map GLB stays loaded — the
    // feature is pl_upward-only, so the map does not change.
    setPlaying(false);
    hideAllViews();
    for (const view of views.values()) {
      view.group.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (mesh.isMesh) {
          mesh.geometry.dispose();
          const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
          for (const mat of mats) mat?.dispose();
        }
      });
      view.group.removeFromParent();
      view.label.texture.dispose();
    }
    views.clear();
    state.selectedEntity = null;
    state.cameraMode = 'free';

    setupReplay(replay);
    updateModeButtons();
    resetFreeCamera();
    updateHud();
    setLoadState('ok');
    hideOverlay();
  } catch (err) {
    setLoadState('err');
    showOverlay([t('errDemo', { msg: (err as Error).message })]);
  } finally {
    demoFileBtn.classList.remove('busy');
    applyLang(); // restore the button label
    demoFileInput.value = ''; // allow re-picking the same file
  }
}

export async function boot(): Promise<void> {
  applyLang(); // sets initial language on static DOM before anything renders
  resetFreeCamera();
  updateModeButtons();
  setPlaying(false);
  hideAllViews();
  updateHud();

  const [mapResult, replayResult] = await Promise.allSettled([loadMap(), loadReplay()]);
  const errors: string[] = [];

  if (mapResult.status === 'rejected') {
    errors.push(t('errMap', { msg: (mapResult.reason as Error).message }));
  }
  if (replayResult.status === 'rejected') {
    errors.push(t('errReplay', { msg: (replayResult.reason as Error).message }));
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
// UI events (same registration order as main.ts)
// ---------------------------------------------------------------------------

playBtn.addEventListener('click', () => {
  togglePlayOrRestart();
  renderer.domElement.focus();
});

speedSelect.addEventListener('change', () => {
  state.speed = Number(speedSelect.value) || 1;
});

timeline.addEventListener('input', () => {
  if (!state.loaded) return;
  setPlaying(false);
  setTick(Number(timeline.value));
});

freeBtn.addEventListener('click', () => {
  state.cameraMode = 'free';
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
  state.hideDead = hideDeadInput.checked;
  applyReplayPoses();
  renderPlayers(true);
});

dimPvsInput.addEventListener('change', () => {
  state.dimPvs = dimPvsInput.checked;
  applyReplayPoses();
});

showModelsInput.addEventListener('change', () => {
  state.showModels = showModelsInput.checked;
  applyReplayPoses();
});

hideFoliageInput.addEventListener('change', () => {
  for (const mesh of state.foliageMeshes) mesh.visible = !hideFoliageInput.checked;
});

// Wireframe toggle: flips material.wireframe on every map + foliage mesh
// (material arrays included — one glTF mesh may carry several primitives).
// The geometry is already in the loaded GLB, so this costs nothing extra.
function applyWireframe(): void {
  const enabled = wireframeInput.checked;
  for (const mesh of [...state.mapMeshes, ...state.foliageMeshes]) {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of materials) {
      if (mat && 'wireframe' in mat) {
        (mat as THREE.MeshStandardMaterial).wireframe = enabled;
      }
    }
  }
}
wireframeInput.addEventListener('change', applyWireframe);

// Prop bounding-box overlay visibility (built once in loadMap).
let propBoxOverlay: THREE.LineSegments | null = null;
propBoxesInput.addEventListener('change', () => {
  if (propBoxOverlay) propBoxOverlay.visible = propBoxesInput.checked;
});

reloadBtn.addEventListener('click', () => window.location.reload());
dismissBtn.addEventListener('click', () => hideOverlay());

demoFileInput.addEventListener('change', () => {
  const file = demoFileInput.files?.[0];
  if (file) void loadDemoFile(file);
});

// ---------------------------------------------------------------------------
// Animation loop
// ---------------------------------------------------------------------------

const clock = new THREE.Clock();
let lastFrameTime = performance.now();

export function animate(): void {
  requestAnimationFrame(animate);

  const now = performance.now();
  const realDt = Math.min(0.1, (now - lastFrameTime) / 1000);
  lastFrameTime = now;
  const dt = clock.getDelta();

  if (state.loaded && state.playing) {
    state.playbackTick += dt * state.loaded.tickRate * state.speed;
    if (state.playbackTick >= state.loaded.maxTick) {
      state.playbackTick = state.loaded.maxTick;
      setPlaying(false);
    }
    timeline.value = String(state.playbackTick);
    updateTimelineUi();
    applyReplayPoses();
    renderPlayers();
  }

  if (state.loaded) {
    renderKillfeed();
    // keep HP/team list in sync even while paused
    if (!state.playing) renderPlayers();
  }

  // updateCamera's fallback path may flip cameraMode back to 'free' when the
  // followed entity left the frame; refresh the mode buttons to match (the
  // original main.ts called updateModeButtons() inline inside updateCamera).
  const modeBefore = state.cameraMode;
  updateCamera(realDt);
  if (modeBefore !== state.cameraMode) updateModeButtons();
  renderer.render(scene, camera);
}

// Real static-prop meshes: replaces the grey boulder boxes baked into the map
// GLB with instanced copies of the exported prop models.
//
// Wiring: tools/add_props_glb.py keeps the compact extras entry for every prop
// (name, box center/size) and, for props whose model was exported by
// tools/props2glb.py, also stores the instance transform (`o` base origin,
// `a` pitch/yaw/roll, `k` scale, `m` full model id) and drops the box geometry.
// The prop GLBs are listed in web/public/models/props/manifest.json.
//
// Axis conventions:
//   * map scene root (gltf.scene) is rotated -PI/2 about X, so its LOCAL frame
//     is Source Z-up; instance transforms are authored in that frame.
//   * props2glb/mdl2glb export model vertices with (x, y, z) -> (z, y, -x).
//     Unlike player models, prop VVDs are Source Z-up (the model base sits at
//     z = 0), so the exported GLB has up = +X. The inverse conversion is
//     therefore C: (X, Y, Z)_export -> (-Z, Y, X)_source, which restores the
//     model base to the XY plane and keeps +Z up.
//   * Source QAngle rotation is R = Rz(yaw) * Ry(pitch) * Rx(roll) in Source
//     axes (same as rotation_matrix in tools/add_props_glb.py).
//   M = T(o) * R * S(k) * C, relative to the map scene root.

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { BASE } from './state';
import { hideFoliageInput } from './dom';

const MODELS_BASE = `${BASE}models/props/`;
const MANIFEST_URL = `${MODELS_BASE}manifest.json`;

interface PropExtrasEntry {
  n?: string;
  p?: number[];
  s?: number[];
  /** Base origin in Source coords (Z-up). */
  o?: number[];
  /** Source QAngle [pitch, yaw, roll] in degrees. */
  a?: number[];
  /** Uniform model scale. */
  k?: number;
  /** Full model id, e.g. "models/props_coalmines/boulder_pile2". */
  m?: string;
  /**
   * Baked per-prop tint (nearest lightmapped face average light) in
   * display-referred sRGB. Used as the instance colour of the real mesh so it
   * matches the baked world lighting. Absent on older extras => white.
   */
  c?: number[];
  /**
   * Foliage flag written by tools/add_props_glb.py via is_foliage(); the GLB
   * is the single source of truth (absent on older extras => not foliage).
   */
  f?: boolean;
}

interface ManifestModel {
  file?: unknown;
  tris?: unknown;
}

interface Manifest {
  version?: unknown;
  models?: Record<string, ManifestModel>;
}

interface PlacedProp {
  model: string;
  origin: [number, number, number];
  angles: [number, number, number];
  scale: number;
  /** Baked tint in display-referred sRGB (white when the extras lack `c`). */
  tint: [number, number, number];
  /** True when the GLB extras carry `f: true` (hideable foliage prop). */
  foliage: boolean;
}

/** Export (Y-up asset frame, up = +X for props) -> Source Z-up. */
const EXPORT_TO_SOURCE = new THREE.Matrix4().set(
  0, 0, -1, 0,
  0, 1, 0, 0,
  1, 0, 0, 0,
  0, 0, 0, 1,
);

/** Untinted instance color for translucent (BLEND) prop materials. */
const WHITE_INSTANCE_TINT = new THREE.Color(1, 1, 1);

function isVec3(value: unknown): value is number[] {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every((v) => Number.isFinite(Number(v)))
  );
}

/** Instance matrix in Source coords, relative to the rotated map scene root. */
export function buildInstanceMatrix(
  origin: [number, number, number],
  angles: [number, number, number],
  scale: number,
): THREE.Matrix4 {
  const [pitch, yaw, roll] = angles.map(THREE.MathUtils.degToRad) as [
    number,
    number,
    number,
  ];
  const rotation = new THREE.Matrix4()
    .makeRotationZ(yaw)
    .multiply(new THREE.Matrix4().makeRotationY(pitch))
    .multiply(new THREE.Matrix4().makeRotationX(roll));
  const translation = new THREE.Matrix4().makeTranslation(
    origin[0],
    origin[1],
    origin[2],
  );
  return translation
    .multiply(rotation)
    .multiply(new THREE.Matrix4().makeScale(scale, scale, scale))
    .multiply(EXPORT_TO_SOURCE);
}

/**
 * Collect per-instance placement data from the map node extras. Same lookup
 * as collectPropEntries in inspect.ts (GLTFLoader copies node.extras into
 * userData); entries without a model id or transform are left to the boxes.
 */
export function collectPlacedProps(root: THREE.Object3D): PlacedProp[] {
  const placed: PlacedProp[] = [];
  root.traverse((obj) => {
    const data = obj.userData as {
      props?: unknown[];
      extras?: { props?: unknown[] };
    };
    const props = data.props ?? data.extras?.props;
    if (!Array.isArray(props)) return;
    for (const raw of props) {
      const e = raw as PropExtrasEntry;
      if (typeof e?.m !== 'string' || !e.m) continue;
      if (!isVec3(e.o) || !isVec3(e.a)) continue;
      const k = Number(e.k);
      if (!Number.isFinite(k) || k <= 0) continue;
      // Baked tint is optional (older extras); white leaves the model as-is.
      const tint: [number, number, number] = isVec3(e.c)
        ? [
            THREE.MathUtils.clamp(Number(e.c[0]), 0, 1),
            THREE.MathUtils.clamp(Number(e.c[1]), 0, 1),
            THREE.MathUtils.clamp(Number(e.c[2]), 0, 1),
          ]
        : [1, 1, 1];
      placed.push({
        model: e.m.replace(/\\/g, '/').replace(/\.mdl$/i, ''),
        origin: [Number(e.o[0]), Number(e.o[1]), Number(e.o[2])],
        angles: [Number(e.a[0]), Number(e.a[1]), Number(e.a[2])],
        scale: k,
        tint,
        // Backward compatibility: extras from older exporters have no flag.
        foliage: e.f === true,
      });
    }
  });
  return placed;
}

/**
 * GLTFLoader marks glTF alphaMode BLEND as `transparent = true`. Those props
 * (light cones, glass) must not be dimmed by the baked tint.
 */
function isBlendMaterial(material: THREE.Material): boolean {
  return material.transparent === true;
}

/**
 * Build an unlit material for a prop mesh. The world is drawn unlit with a
 * baked lightmap, so props must ignore the scene's hemisphere/fill lights too;
 * the baked tint arrives per instance through InstancedMesh.instanceColor.
 */
function toUnlitMaterial(source: THREE.Material): THREE.MeshBasicMaterial {
  const src = source as THREE.MeshStandardMaterial;
  const material = new THREE.MeshBasicMaterial({
    map: src.map ?? null,
    color: src.color ? src.color.clone() : new THREE.Color(1, 1, 1),
    side: src.side,
    fog: src.fog,
    alphaTest: src.alphaTest,
    transparent: src.transparent,
    opacity: src.opacity,
    premultipliedAlpha: src.premultipliedAlpha,
    depthWrite: src.depthWrite,
  });
  material.name = src.name;

  // three r186's color_fragment.glsl only multiplies diffuseColor by vColor
  // under USE_COLOR/USE_COLOR_ALPHA; USE_INSTANCING_COLOR alone can therefore
  // leave the fragment output untinted. Inject the multiply when the chunk did
  // not already do it (the guard keeps it from doubling the tint on versions
  // where instancing colors also define USE_COLOR).
  material.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <color_fragment>',
      `#include <color_fragment>
#if defined( USE_INSTANCING_COLOR ) && !defined( USE_COLOR ) && !defined( USE_COLOR_ALPHA )
	diffuseColor.rgb *= vColor.rgb;
#endif`,
    );
  };
  // Keep this injected program separate from other MeshBasicMaterials.
  material.customProgramCacheKey = () => 'prop-baked-unlit';
  return material;
}

/** Display-referred sRGB tint -> renderer working (linear) color. */
function tintToLinear(tint: [number, number, number]): THREE.Color {
  return new THREE.Color().setRGB(tint[0], tint[1], tint[2], THREE.SRGBColorSpace);
}

/**
 * Load the prop model manifest and instance every placed prop whose model is
 * listed there. Never throws: a missing/broken manifest or individual model
 * leaves the baked box geometry in place.
 *
 * @param root       rotated map scene root; instanced meshes are added here.
 * @param extrasRoot object tree that carries the prop extras (usually the same
 *                   root, but kept separate because extras live on node 0).
 * @param onMeshes   called once with the instanced meshes that were added:
 *                   (visible map meshes, hideable foliage meshes). Registering
 *                   the second list lets the hide-foliage checkbox and the
 *                   wireframe toggle drive them like the baked foliage mesh.
 */
export async function loadPropModels(
  root: THREE.Object3D,
  extrasRoot: THREE.Object3D,
  onMeshes: (
    meshes: THREE.InstancedMesh[],
    foliageMeshes: THREE.InstancedMesh[],
  ) => void,
): Promise<void> {
  const placed = collectPlacedProps(extrasRoot);
  if (placed.length === 0) return;

  let manifest: Manifest;
  try {
    const response = await fetch(MANIFEST_URL);
    if (!response.ok) {
      console.warn(
        `[props] manifest not available (${response.status}); keeping boxes`,
      );
      return;
    }
    manifest = (await response.json()) as Manifest;
  } catch (err) {
    console.warn('[props] manifest read failed; keeping boxes', err);
    return;
  }
  if (!manifest || typeof manifest !== 'object' || !manifest.models) {
    console.warn('[props] manifest has no "models" table; keeping boxes');
    return;
  }
  const models = manifest.models;

  // Group placements by model so each GLB is loaded once and every repeating
  // instance becomes one InstancedMesh draw call per mesh primitive. Foliage
  // and non-foliage placements are kept apart because an InstancedMesh has a
  // single visibility state (the checkbox toggles foliage meshes only).
  const groups = new Map<
    string,
    { model: string; foliage: boolean; list: PlacedProp[] }
  >();
  for (const prop of placed) {
    if (!(prop.model in models)) continue;
    const key = `${prop.foliage ? 'f' : 'p'}:${prop.model}`;
    const group = groups.get(key);
    if (group) group.list.push(prop);
    else groups.set(key, { model: prop.model, foliage: prop.foliage, list: [prop] });
  }
  if (groups.size === 0) return;

  const loader = new GLTFLoader();
  const added: THREE.InstancedMesh[] = [];
  const addedFoliage: THREE.InstancedMesh[] = [];

  await Promise.all(
    [...groups.values()].map(async ({ model, foliage, list }) => {
      const entry = models[model];
      const file = entry?.file;
      if (typeof file !== 'string' || !file) {
        console.warn(`[props] ${model}: manifest entry has no file`);
        return;
      }
      let gltf: Awaited<ReturnType<GLTFLoader['loadAsync']>>;
      try {
        gltf = await loader.loadAsync(`${MODELS_BASE}${file}`);
      } catch (err) {
        console.warn(`[props] failed to load ${model} (${file}); keeping box`, err);
        return;
      }

      gltf.scene.updateMatrixWorld(true);
      const parts: {
        geometry: THREE.BufferGeometry;
        material: THREE.MeshBasicMaterial;
        matrix: THREE.Matrix4;
        /** False for BLEND parts, which use a white instance tint. */
        baked: boolean;
      }[] = [];
      gltf.scene.traverse((obj) => {
        if (!(obj instanceof THREE.Mesh) || obj instanceof THREE.SkinnedMesh) return;
        // Preserve any node transform authored inside the prop GLB.
        const matrix = obj.matrixWorld.clone();
        const build = (source: THREE.Material, geometry: THREE.BufferGeometry) => {
          parts.push({
            geometry,
            material: toUnlitMaterial(source),
            matrix,
            baked: !isBlendMaterial(source),
          });
        };
        const materials = Array.isArray(obj.material)
          ? obj.material
          : [obj.material];
        const groups = obj.geometry.groups;
        if (materials.length <= 1 || groups.length === 0) {
          if (materials[0]) build(materials[0], obj.geometry);
          return;
        }
        // A multi-primitive glTF mesh becomes one geometry plus material
        // groups. Split it so each InstancedMesh has a single material and can
        // pick its own instance tint (BLEND white, opaque/MASK baked).
        for (const group of groups) {
          const source = materials[group.materialIndex ?? 0] ?? materials[0];
          if (!source) continue;
          const geometry = new THREE.BufferGeometry();
          for (const name of Object.keys(obj.geometry.attributes)) {
            geometry.setAttribute(name, obj.geometry.getAttribute(name));
          }
          if (obj.geometry.index) geometry.setIndex(obj.geometry.index);
          geometry.setDrawRange(group.start, group.count);
          build(source, geometry);
        }
      });
      if (parts.length === 0) {
        console.warn(`[props] ${model}: GLB contains no static meshes`);
        return;
      }

      const matrices = list.map((prop) =>
        buildInstanceMatrix(prop.origin, prop.angles, prop.scale),
      );
      const bakedColors = list.map((prop) => tintToLinear(prop.tint));

      for (const part of parts) {
        const mesh = new THREE.InstancedMesh(
          part.geometry,
          part.material,
          list.length,
        );
        mesh.name = `propModel:${model}`;
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        // Foliage instances start in the state the hide-foliage checkbox is
        // in; everything else is unconditionally visible.
        mesh.visible = foliage ? !hideFoliageInput.checked : true;
        const matrix = new THREE.Matrix4();
        for (let i = 0; i < matrices.length; i++) {
          matrix.copy(matrices[i]).multiply(part.matrix);
          mesh.setMatrixAt(i, matrix);
          // Per-instance baked light; BLEND props stay untinted (white).
          mesh.setColorAt(i, part.baked ? bakedColors[i] : WHITE_INSTANCE_TINT);
        }
        mesh.instanceMatrix.needsUpdate = true;
        if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
        mesh.computeBoundingSphere();
        root.add(mesh);
        if (foliage) addedFoliage.push(mesh);
        else added.push(mesh);
      }
    }),
  );

  if (added.length > 0 || addedFoliage.length > 0) {
    onMeshes(added, addedFoliage);
    const drawCalls = added.length + addedFoliage.length;
    console.log(
      `[props] instanced ${placed.length} placement(s) across ${groups.size} model(s)` +
        ` in ${drawCalls} draw call(s) (${addedFoliage.length} foliage)`,
    );
  }
}

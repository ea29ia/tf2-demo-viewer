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
}

/** Export (Y-up asset frame, up = +X for props) -> Source Z-up. */
const EXPORT_TO_SOURCE = new THREE.Matrix4().set(
  0, 0, -1, 0,
  0, 1, 0, 0,
  1, 0, 0, 0,
  0, 0, 0, 1,
);

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
      placed.push({
        model: e.m.replace(/\\/g, '/').replace(/\.mdl$/i, ''),
        origin: [Number(e.o[0]), Number(e.o[1]), Number(e.o[2])],
        angles: [Number(e.a[0]), Number(e.a[1]), Number(e.a[2])],
        scale: k,
      });
    }
  });
  return placed;
}

/**
 * Load the prop model manifest and instance every placed prop whose model is
 * listed there. Never throws: a missing/broken manifest or individual model
 * leaves the baked box geometry in place.
 *
 * @param root       rotated map scene root; instanced meshes are added here.
 * @param extrasRoot object tree that carries the prop extras (usually the same
 *                   root, but kept separate because extras live on node 0).
 * @param onMeshes   called once with the instanced meshes that were added, so
 *                   the caller can register them for raycasts/wireframe.
 */
export async function loadPropModels(
  root: THREE.Object3D,
  extrasRoot: THREE.Object3D,
  onMeshes: (meshes: THREE.InstancedMesh[]) => void,
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
  // instance becomes one InstancedMesh draw call per mesh primitive.
  const groups = new Map<string, PlacedProp[]>();
  for (const prop of placed) {
    if (!(prop.model in models)) continue;
    const list = groups.get(prop.model);
    if (list) list.push(prop);
    else groups.set(prop.model, [prop]);
  }
  if (groups.size === 0) return;

  const loader = new GLTFLoader();
  const added: THREE.InstancedMesh[] = [];

  await Promise.all(
    [...groups].map(async ([model, list]) => {
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
        material: THREE.Material | THREE.Material[];
        matrix: THREE.Matrix4;
      }[] = [];
      gltf.scene.traverse((obj) => {
        if (!(obj instanceof THREE.Mesh) || obj instanceof THREE.SkinnedMesh) return;
        parts.push({
          geometry: obj.geometry,
          material: obj.material,
          // Preserve any node transform authored inside the prop GLB.
          matrix: obj.matrixWorld.clone(),
        });
      });
      if (parts.length === 0) {
        console.warn(`[props] ${model}: GLB contains no static meshes`);
        return;
      }

      const matrices = list.map((prop) =>
        buildInstanceMatrix(prop.origin, prop.angles, prop.scale),
      );

      for (const part of parts) {
        const mesh = new THREE.InstancedMesh(
          part.geometry,
          part.material,
          list.length,
        );
        mesh.name = `propModel:${model}`;
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        const matrix = new THREE.Matrix4();
        for (let i = 0; i < matrices.length; i++) {
          matrix.copy(matrices[i]).multiply(part.matrix);
          mesh.setMatrixAt(i, matrix);
        }
        mesh.instanceMatrix.needsUpdate = true;
        mesh.computeBoundingSphere();
        root.add(mesh);
        added.push(mesh);
      }
    }),
  );

  if (added.length > 0) {
    onMeshes(added);
    console.log(
      `[props] instanced ${placed.length} placement(s) across ${groups.size} model(s) in ${added.length} draw call(s)`,
    );
  }
}

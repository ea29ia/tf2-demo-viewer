import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

// Real player models (converted from the local TF2 VPK install by
// tools/mdl2glb.py — static glTF, bind pose, no skins).
// Y-up, feet at y=0, ~80 units tall (measured scout bbox: 81.6).

// Order follows the dumper's parser enum (analyser::Class): scout, sniper,
// soldier, demoman, medic, heavy, pyro, spy, engineer — NOT the wiki order.
export const CLASS_NAMES = [
  'scout',
  'sniper',
  'soldier',
  'demoman',
  'medic',
  'heavy',
  'pyro',
  'spy',
  'engineer',
] as const;

export const TEAM_NAMES = ['red', 'blue'] as const;

export interface PlayerModelInstance {
  group: THREE.Group;
  materials: THREE.MeshStandardMaterial[];
}

interface PlayerModelTemplate {
  scene: THREE.Object3D;
  /** Vertical offset applied so the feet sit exactly at the group origin. */
  yOffset: number;
}

const templates = new Map<string, PlayerModelTemplate | null>();
const pending = new Map<string, Promise<PlayerModelTemplate | null>>();
const warned = new Set<string>();

const loader = new GLTFLoader();

const BASE = import.meta.env.BASE_URL;

function modelUrl(cls: number, team: number): string | null {
  const name = CLASS_NAMES[cls - 1];
  if (!name) return null;
  const teamName = team === 2 ? 'red' : team === 3 ? 'blue' : null;
  if (!teamName) return null;
  return `${BASE}models/players/${name}_${teamName}.glb`;
}

/**
 * Returns a template for (cls, team), or null when unavailable / failed.
 * Failures are logged once per key and cached as null (fallback to capsule).
 */
export function getModelTemplate(cls: number, team: number): Promise<PlayerModelTemplate | null> {
  const key = `${cls}_${team}`;
  const cached = templates.get(key);
  if (cached !== undefined) return Promise.resolve(cached);
  const inFlight = pending.get(key);
  if (inFlight) return inFlight;

  const url = modelUrl(cls, team);
  if (!url) {
    templates.set(key, null);
    return Promise.resolve(null);
  }

  const promise = loader
    .loadAsync(url)
    .then((gltf) => {
      const scene = gltf.scene;
      const bbox = new THREE.Box3().setFromObject(scene);
      const template: PlayerModelTemplate = {
        scene,
        // Feet to group origin (models already have feet near y=0, but be exact).
        yOffset: -bbox.min.y,
      };
      templates.set(key, template);
      pending.delete(key);
      return template;
    })
    .catch((err: unknown) => {
      if (!warned.has(key)) {
        warned.add(key);
        console.warn(`playerModels: failed to load ${url}, falling back to capsule`, err);
      }
      templates.set(key, null);
      pending.delete(key);
      return null;
    });
  pending.set(key, promise);
  return promise;
}

/** Clone the template scene for one player; materials are cloned per instance
 * so per-player opacity/emissive tinting never leaks between players. */
export function instantiateModel(
  template: PlayerModelTemplate,
): PlayerModelInstance {
  const group = new THREE.Group();
  const clone = template.scene.clone(true);
  const materials: THREE.MeshStandardMaterial[] = [];
  const oldToNew = new Map<THREE.Material | THREE.Material[], THREE.Material>();
  clone.traverse((obj) => {
    if (!(obj instanceof THREE.Mesh)) return;
    obj.castShadow = true;
    obj.receiveShadow = false;
    const srcMat = obj.material as THREE.Material | THREE.Material[];
    let mat = oldToNew.get(srcMat);
    if (!mat) {
      mat = (srcMat as THREE.MeshStandardMaterial).clone();
      mat.transparent = true;
      oldToNew.set(srcMat, mat);
    }
    obj.material = mat;
    if (Array.isArray(mat)) materials.push(...(mat as THREE.MeshStandardMaterial[]));
    else materials.push(mat as THREE.MeshStandardMaterial);
  });
  clone.position.y = template.yOffset;
  group.add(clone);
  return { group, materials };
}

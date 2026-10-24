// Kit pickup state overlay. The static kit pads (green health / amber ammo)
// are baked into the map GLB by tools/add_kits_glb.py from web/public/maps/
// kits.json; that GLB cannot change during playback, so the DEMO-driven
// present-vs-picked-up state is rendered as a separate floating marker per
// kit: visible only while the kit is present (replay v4 `kits` block,
// feature-detected — with a v3 replay nothing is shown and the static pads
// keep their vanilla look).

import * as THREE from 'three';
import { findFrameIndex, type KitRecord } from '../replay';
import { state, BASE } from './state';
import { kitGroup } from './scene';

// NOTE: KitRecord is imported from ../replay (the canonical v4 type). The
// per-frame code still guards with Array.isArray so a v3-shaped frame object
// (no kits array) is a no-op rather than a crash.

export const KFLAG_PRESENT = 1 << 0;

const HEALTH_COLOR = 0x3ddc63;
const AMMO_COLOR = 0xffb340;
// Marker half-size per kind (small / medium / full).
const KIND_SIZE = [9, 12, 15, 9, 12, 15];

interface KitMarker {
  mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
  x: number; // scene coords
  y: number;
  z: number;
  kind: number;
}

const kitMarkers: (KitMarker | null)[] = [];
let loaded = false;

interface KitJsonEntry {
  class: string;
  x: number;
  y: number;
  z: number;
}

/** kits.json class name -> contract kind number (0..5). */
function kindFromClass(name: string): number {
  const n = name.toLowerCase();
  const isHealth = n.includes('health');
  const size = n.includes('full') ? 2 : n.includes('medium') ? 1 : 0;
  return isHealth ? size : 3 + size;
}

function kindColor(kind: number): number {
  return kind <= 2 ? HEALTH_COLOR : AMMO_COLOR;
}

/**
 * Load kits.json and build one marker mesh per entry. Fire-and-forget from
 * loadMap(): a missing or malformed kits.json only means no demo-state
 * overlay (console warn), never a viewer failure.
 */
export async function loadKits(): Promise<void> {
  if (loaded) return;
  loaded = true;
  const response = await fetch(`${BASE}maps/kits.json`, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`kits.json: HTTP ${response.status}`);
  const entries = (await response.json()) as KitJsonEntry[];
  if (!Array.isArray(entries)) throw new Error('kits.json is not an array');

  const geometry = new THREE.OctahedronGeometry(1, 0);
  entries.forEach((entry, index) => {
    if (!entry || typeof entry.x !== 'number' || typeof entry.y !== 'number') return;
    const kind = typeof entry.class === 'string' ? kindFromClass(entry.class) : 1;
    const material = new THREE.MeshStandardMaterial({
      color: kindColor(kind),
      emissive: kindColor(kind),
      emissiveIntensity: 0.9,
      roughness: 0.35,
      transparent: true,
      opacity: 0.92,
    });
    const mesh = new THREE.Mesh(geometry, material);
    const size = KIND_SIZE[clampKind(kind)] ?? 12;
    mesh.scale.setScalar(size);
    // Source Z-up -> scene Y-up: (x, y, z) -> (x, z, -y); hover above the pad.
    const z = typeof entry.z === 'number' ? entry.z : 0;
    mesh.position.set(entry.x, z + 26 + size, -entry.y);
    mesh.visible = false; // applyKits() drives visibility per frame
    kitGroup.add(mesh);
    // Positional storage: kitMarkers[record.index] must be this entry even if
    // some entries were skipped above.
    kitMarkers[index] = { mesh, x: entry.x, y: entry.y, z, kind };
  });
  const built = kitMarkers.filter(Boolean).length;
  console.log('[kits] markers built:', built);
}

function clampKind(kind: number): number {
  return Math.min(5, Math.max(0, Math.floor(kind)));
}

/**
 * Per-frame kit visibility from the replay `kits` block. The join key is the
 * record's `index` == kits.json array index. A v3 replay (no `kits`) leaves
 * the markers hidden — the static GLB pads remain the only kit visuals.
 */
export function applyKits(): void {
  if (!state.loaded || kitMarkers.length === 0) return;
  const frames = state.loaded.replay.frames;
  if (frames.length === 0) return;
  const frame = frames[findFrameIndex(frames, state.playbackTick)];
  const kits = frame?.kits;
  if (!Array.isArray(kits)) return;

  for (const marker of kitMarkers) {
    if (marker) marker.mesh.visible = false;
  }
  for (const raw of kits) {
    if (!raw || typeof raw.index !== 'number') continue;
    const marker = kitMarkers[raw.index];
    if (!marker) continue;
    const flags = typeof raw.flags === 'number' ? raw.flags : KFLAG_PRESENT;
    if ((flags & KFLAG_PRESENT) === 0) continue; // picked up -> no marker
    // Prefer the record position (should match kits.json); fall back to it.
    const x = Number.isFinite(raw.x) ? (raw.x as number) : marker.x;
    const y = Number.isFinite(raw.y) ? (raw.y as number) : marker.y;
    const z = Number.isFinite(raw.z) ? (raw.z as number) : marker.z;
    marker.mesh.position.set(x, z + 26 + (KIND_SIZE[clampKind(raw.kind)] ?? 12), -y);
    marker.mesh.visible = true;
  }
}

// Click/tap-to-inspect prop info card, extracted from main.ts
// (behavior-preserving port; main.ts remains the source of truth until the
// wiring is switched over).

import * as THREE from 'three';
import { inspectCard, inspectTitle, inspectLabel, inspectMeta, inspectClose } from './dom';
import { renderer, camera } from './scene';
import { state } from './state';
import { activePointers } from './input';
import { t } from '../i18n';

// ---------------------------------------------------------------------------
// Prop click-to-inspect
// ---------------------------------------------------------------------------

interface PropEntry {
  name: string;
  label: string;
  pos: THREE.Vector3; // scene coords (Source x,z,-y), box center
  tol: number; // match radius: half diagonal + 16 units
}

let propEntries: PropEntry[] = [];

function propLabel(name: string): string {
  if (name.startsWith('kit:')) {
    return name.toLowerCase().includes('health') ? t('propHealthkit') : t('propAmmo');
  }
  const n = name.toLowerCase();
  if (n.includes('track')) return t('propTrack');
  if (n.includes('handrail')) return t('propHandrail');
  if (n.includes('boulder') || n.includes('rock')) return t('propRock');
  if (n.includes('oildrum') || n.includes('barrel') || n.includes('drum')) return t('propBarrel');
  if (n.includes('crate')) return t('propCrate');
  if (n.includes('fence')) return t('propFence');
  if (n.includes('shrub') || n.includes('bush') || n.includes('grass')) return t('propShrub');
  if (n.includes('light') || n.includes('lamp')) return t('propLight');
  if (n.includes('tree')) return t('propTree');
  return '';
}

export function collectPropEntries(root: THREE.Object3D): number {
  propEntries = [];
  root.traverse((obj) => {
    const extras = (obj.userData as { extras?: { props?: unknown[] } }).extras;
    const props = extras?.props;
    if (!Array.isArray(props)) return;
    for (const raw of props) {
      const e = raw as { n?: string; p?: number[]; s?: number[] };
      if (!e?.n || !Array.isArray(e.p) || e.p.length !== 3) continue;
      if (!Array.isArray(e.s) || e.s.length !== 3) continue;
      const px = Number(e.p[0]);
      const py = Number(e.p[1]);
      const pz = Number(e.p[2]);
      const sx = Number(e.s[0]);
      const sy = Number(e.s[1]);
      const sz = Number(e.s[2]);
      if (![px, py, pz, sx, sy, sz].every(Number.isFinite)) continue;
      // Source Z-up -> scene Y-up: (x, y, z) -> (x, z, -y)
      propEntries.push({
        name: e.n,
        label: propLabel(e.n),
        pos: new THREE.Vector3(px, pz, -py),
        tol: 0.5 * Math.hypot(sx, sy, sz) + 16,
      });
    }
  });
  return propEntries.length;
}

function hideInspect(): void {
  inspectCard.classList.add('hidden');
}

function showInspect(entry: PropEntry): void {
  const slash = entry.name.lastIndexOf('/');
  inspectTitle.textContent = slash >= 0 ? entry.name.slice(slash + 1) : entry.name;
  inspectLabel.textContent = propLabel(entry.name) || t('propDefault');
  // Scene coords back to Source units for display.
  const sx = Math.round(entry.pos.x);
  const sy = Math.round(-entry.pos.z);
  const sz = Math.round(entry.pos.y);
  inspectMeta.textContent = t('inspectPos', { x: sx, y: sy, z: sz });
  inspectCard.classList.remove('hidden');
}

// Dedicated raycaster for inspect: the camera-collision code in camera.ts
// mutates its own raycaster's `.far` (shared-state bug, see
// docs/agent-notes/30-viewer-internals.md), so inspect must not share it.
const raycaster = new THREE.Raycaster();

function inspectAt(clientX: number, clientY: number): void {
  const rect = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(
    ((clientX - rect.left) / rect.width) * 2 - 1,
    -((clientY - rect.top) / rect.height) * 2 + 1,
  );
  raycaster.setFromCamera(ndc, camera);
  raycaster.far = Infinity; // camera-collision code sets .far; reset it here (see docs/agent-notes/30-viewer-internals.md)
  const targets = [...state.mapMeshes, ...state.foliageMeshes.filter((m) => m.visible)];
  const hits = raycaster.intersectObjects(targets, false);
  let best: PropEntry | null = null;
  let bestDist = Infinity;
  if (hits.length > 0) {
    const point = hits[0].point;
    for (const entry of propEntries) {
      const d = entry.pos.distanceTo(point);
      if (d < entry.tol && d < bestDist) {
        bestDist = d;
        best = entry;
      }
    }
  }
  if (!best && propEntries.length > 0) {
    // Fallback: nearest object to the click RAY (helps when the click lands on
    // terrain next to a prop, or the geometry is thin).
    const ray = raycaster.ray;
    for (const entry of propEntries) {
      const d = ray.distanceToPoint(entry.pos);
      if (d < entry.tol && d < bestDist) {
        bestDist = d;
        best = entry;
      }
    }
  }
  if (best) showInspect(best);
  else hideInspect();
}

inspectClose.addEventListener('click', hideInspect);

// Tap/click-to-inspect: only when the pointer didn't drag (>6 px between
// down/up). Right-drag look and touch one-finger look stay unaffected.
interface TapStart {
  id: number;
  x: number;
  y: number;
  moved: number;
  valid: boolean;
}
let tap: TapStart | null = null;

renderer.domElement.addEventListener('pointerdown', (event) => {
  const valid =
    event.pointerType === 'touch'
      ? activePointers.size <= 1 // second finger = pinch, not a tap
      : event.button === 0;
  tap = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: 0, valid };
});

window.addEventListener(
  'pointermove',
  (event) => {
    if (!tap || event.pointerId !== tap.id) return;
    tap.moved = Math.max(tap.moved, Math.hypot(event.clientX - tap.x, event.clientY - tap.y));
    if (tap.moved > 6) tap.valid = false;
  },
  { passive: true },
);

window.addEventListener('pointerup', (event) => {
  const t = tap;
  tap = null;
  if (!t || event.pointerId !== t.id || !t.valid || t.moved > 6) return;
  inspectAt(event.clientX, event.clientY);
});

window.addEventListener('pointercancel', () => {
  tap = null;
});

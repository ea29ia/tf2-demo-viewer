// Projectile view management: pooled meshes, per-kind geometry, frame interpolation.
// Extracted from main.ts (behavior-preserving port).

import * as THREE from 'three';
import type { ProjectileRecord } from '../replay';
import { findFrameIndex } from '../replay';
import { teamColor, clamp, lerpN } from './tf2';
import { state } from './state';
import { getProjMap } from './frameCache';
import { projectileGroup } from './scene';
import { spawnSplash } from './damageFx';
import { t } from '../i18n';

interface ProjView {
  mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
  kind: number;
  critical: boolean;
  team: number;
  used: boolean;
  lastSeenTick: number;
  lastX: number;
  lastY: number;
  lastZ: number;
}

const projViews = new Map<number, ProjView>();
const PROJ_KIND_KEYS: Record<number, string> = {
  0: 'projRocket',
  1: 'projArrow',
  2: 'projSticky',
  3: 'projBomb',
  4: 'projFlare',
  5: 'projRocket', // Loose Cannon counts as a rocket-type projectile
  7: 'projShell',
};

function projKindLabel(kind: number): string {
  return t(PROJ_KIND_KEYS[kind] ?? 'projShell');
}

const projGeometries: Record<number, THREE.BufferGeometry> = {};
function projGeometry(kind: number): THREE.BufferGeometry {
  if (!projGeometries[kind]) {
    if (kind === 0) {
      // rocket — slim capsule oriented along velocity
      projGeometries[kind] = new THREE.CapsuleGeometry(4.5, 22, 4, 10);
    } else if (kind === 1) {
      projGeometries[kind] = new THREE.CapsuleGeometry(2.2, 30, 4, 8);
    } else if (kind === 2 || kind === 3 || kind === 5) {
      // pipes / stickies — spheres
      projGeometries[kind] = new THREE.SphereGeometry(6.5, 12, 10);
    } else {
      projGeometries[kind] = new THREE.SphereGeometry(4.5, 10, 8);
    }
  }
  return projGeometries[kind];
}

function projViewFor(entity: number): ProjView {
  const existing = projViews.get(entity);
  if (existing) return existing;
  const material = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    emissive: 0xffffff,
    emissiveIntensity: 0.7,
    roughness: 0.4,
    transparent: true,
    opacity: 0.95,
  });
  const mesh = new THREE.Mesh(projGeometry(7), material);
  mesh.visible = false;
  mesh.castShadow = true;
  projectileGroup.add(mesh);
  const view: ProjView = {
    mesh,
    kind: 7,
    critical: false,
    team: 0,
    used: false,
    lastSeenTick: -1,
    lastX: 0,
    lastY: 0,
    lastZ: 0,
  };
  projViews.set(entity, view);
  return view;
}

const tmpProjA = new THREE.Vector3();
const tmpProjB = new THREE.Vector3();
const tmpUp = new THREE.Vector3(0, 1, 0);

export function applyProjectiles(): void {
  if (!state.loaded) return;
  const frames = state.loaded.replay.frames;
  if (frames.length === 0) return;
  const i = findFrameIndex(frames, state.playbackTick);
  const a = frames[i];
  const b = frames[Math.min(i + 1, frames.length - 1)];
  const mapA = getProjMap(a);
  const mapB = getProjMap(b);
  const alpha = a !== b && b.tick > a.tick
    ? clamp((state.playbackTick - a.tick) / (b.tick - a.tick), 0, 1)
    : 0;

  // Merge entity sets: a projectile present in either frame is rendered.
  const seen = new Set<number>();
  for (const [id, pa] of mapA) {
    seen.add(id);
    const pb = mapB.get(id);
    mixProjectile(id, pa, pb, pb ? alpha : 0);
  }
  for (const [id, pb] of mapB) {
    if (seen.has(id)) continue;
    mixProjectile(id, pb, pb, 0);
  }

  for (const [id, view] of projViews) {
    if (!seen.has(id)) {
      // Explosive projectile vanished: spawn a splash ring at its last position.
      if (
        (view.kind === 0 || view.kind === 2 || view.kind === 3 || view.kind === 5) &&
        view.used &&
        view.lastSeenTick >= 0 &&
        state.playbackTick - view.lastSeenTick <= 2
      ) {
        spawnSplash(
          view.lastX,
          view.lastY,
          view.lastZ,
          view.kind === 0 ? 146 : 148,
          state.playbackTick,
        );
      }
      view.mesh.visible = false;
      view.used = false;
      view.lastSeenTick = -1;
    }
  }
}

function mixProjectile(
  id: number,
  a: ProjectileRecord,
  b: ProjectileRecord | undefined,
  t: number,
): void {
  const view = projViewFor(id);
  const target = b ?? a;
  const dx = target.x - a.x;
  const dy = target.y - a.y;
  const dz = target.z - a.z;
  const teleported = b !== undefined && Math.hypot(dx, dy, dz) > 256;
  const mix = teleported ? 1 : t;
  const x = teleported ? target.x : lerpN(a.x, target.x, mix);
  const y = teleported ? target.y : lerpN(a.y, target.y, mix);
  const z = teleported ? target.z : lerpN(a.z, target.z, mix);

  const kind = t < 0.5 ? a.kind : target.kind;
  const team = t < 0.5 ? a.team : target.team;
  const critical = t < 0.5 ? (a.flags & 1) !== 0 : (target.flags & 1) !== 0;

  if (kind !== view.kind || team !== view.team || critical !== view.critical) {
    view.kind = kind;
    view.team = team;
    view.critical = critical;
    view.mesh.geometry = projGeometry(kind);
    const col = teamColor(team);
    view.mesh.material.color.setHex(critical ? 0xffd24a : col);
    view.mesh.material.emissive.setHex(critical ? 0xffaa00 : col);
    view.mesh.material.emissiveIntensity = critical ? 1.4 : 0.7;
  }

  // Source (x,y,z) Z-up -> scene (x, z, -y) Y-up
  view.mesh.position.set(x, z, -y);

  // Orient rockets/arrows along velocity if available
  const vx = target.vx;
  const vy = target.vy;
  const vz = target.vz;
  if (kind === 0 || kind === 1) {
    const v = tmpProjA.set(vx, vz, -vy);
    if (v.lengthSq() > 1) {
      v.normalize();
      view.mesh.quaternion.setFromUnitVectors(tmpUp, v);
    }
  }

  view.mesh.visible = true;
  view.used = true;
  view.lastSeenTick = state.playbackTick;
  view.lastX = x;
  view.lastY = y;
  view.lastZ = z;
}

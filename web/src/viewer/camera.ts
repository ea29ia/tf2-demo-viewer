// Camera logic extracted from main.ts (behavior-preserving port; main.ts
// remains the source of truth until the wiring is switched over).

import * as THREE from 'three';
import { camera, fallbackGrid } from './scene';
import { clamp, EYE_HEIGHT } from './tf2';
import { hud } from './dom';
import { t } from '../i18n';
import { state, type CameraMode } from './state';
import { posesAt } from './frameCache';

const tmpVec = new THREE.Vector3();
const tmpTarget = new THREE.Vector3();

// Module-local raycaster used ONLY for follow-camera wall collision. The
// inspect module keeps its own Raycaster instance — sharing one instance was
// a real bug: the collision code set raycaster.far and the click-inspect ray
// inherited that truncated far plane (see docs/agent-notes/30-viewer-internals.md).
const raycaster = new THREE.Raycaster();

export function resetFreeCamera(): void {
  let center = new THREE.Vector3(0, 0, 0);
  let radius = 5000;
  const mapBounds = state.mapBounds;
  if (mapBounds && !mapBounds.isEmpty()) {
    mapBounds.getCenter(center);
    const size = mapBounds.getSize(new THREE.Vector3());
    radius = Math.max(size.x, size.z, 2000) * 0.5;
  }
  camera.position.copy(center).add(new THREE.Vector3(radius * 0.8, radius * 0.95, radius * 1.1));
  camera.lookAt(center);
  camera.rotation.order = 'YXZ';
  state.freeYaw = camera.rotation.y;
  state.freePitch = camera.rotation.x;
}

export function updateCamera(dt: number): void {
  const { keys, freeYaw, freePitch, touchSpeedMult, joyForward, joySide, elevAxis } = state;
  if (state.cameraMode === 'free') {
    // WASD + Q/E movement (keyboard) merged with virtual joystick axes (touch)
    const boost = keys.has('ShiftLeft') || keys.has('ShiftRight') ? 3.2 : 1;
    const speedUnits = 850 * boost * dt * touchSpeedMult;
    const forward = tmpVec.set(-Math.sin(freeYaw), 0, -Math.cos(freeYaw));
    const right = tmpTarget.set(Math.cos(freeYaw), 0, -Math.sin(freeYaw));

    const fwdAxis = clamp(
      (keys.has('KeyW') ? 1 : 0) - (keys.has('KeyS') ? 1 : 0) + joyForward,
      -1,
      1,
    );
    const sideAxis = clamp((keys.has('KeyD') ? 1 : 0) - (keys.has('KeyA') ? 1 : 0) + joySide, -1, 1);
    const upAxis = clamp((keys.has('KeyE') ? 1 : 0) - (keys.has('KeyQ') ? 1 : 0) + elevAxis, -1, 1);

    if (fwdAxis !== 0) camera.position.addScaledVector(forward, speedUnits * fwdAxis);
    if (sideAxis !== 0) camera.position.addScaledVector(right, speedUnits * sideAxis);
    if (upAxis !== 0) camera.position.y += speedUnits * upAxis;

    camera.rotation.set(freePitch, freeYaw, 0, 'YXZ');
    fallbackGrid.visible = !state.mapBounds;
    return;
  }

  fallbackGrid.visible = !state.mapBounds;
  if (state.selectedEntity === null) {
    // NOTE: in main.ts this path also called updateModeButtons(); that is UI
    // work, so it is left to the wiring layer after the mode write here.
    state.cameraMode = 'free';
    return;
  }
  const pose =
    state.lastPoses.get(state.selectedEntity) ??
    posesAt(state.playbackTick).get(state.selectedEntity);
  if (!pose) {
    hud.textContent = t('hudOutOfFrame', { n: state.selectedEntity });
    return;
  }
  if (!Number.isFinite(pose.x + pose.y + pose.z)) return;

  const feet = tmpVec.set(pose.x, pose.z, -pose.y);
  const yawRad = THREE.MathUtils.degToRad(pose.yaw);

  if (state.cameraMode === 'pov') {
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

  // follow from behind — pinch/wheel adjusts follow distance
  const forward = tmpTarget.set(Math.cos(yawRad), 0, -Math.sin(yawRad));
  const dist = Math.max(80, Math.min(4000, state.followDistance));
  const desired = new THREE.Vector3(
    feet.x - forward.x * dist,
    feet.y + 60 + dist * 0.45,
    feet.z - forward.z * dist,
  );
  if (state.followSnap) {
    camera.position.copy(desired);
    state.followSnap = false;
  } else {
    const smooth = 1 - Math.exp(-9 * dt);
    camera.position.lerp(desired, smooth);
  }
  camera.lookAt(feet.x, feet.y + 62, feet.z);

  // Keep the camera out of map geometry: raycast from the player's head to the
  // camera; if a wall is in the way, pull the camera in front of the hit.
  if (state.mapMeshes.length > 0) {
    const head = tmpVec.set(feet.x, feet.y + 62, feet.z);
    const toCam = camera.position.clone().sub(head);
    const len = toCam.length();
    if (len > 1) {
      toCam.normalize();
      raycaster.set(head, toCam);
      raycaster.far = len;
      const hits = raycaster.intersectObjects(state.mapMeshes, false);
      if (hits.length > 0 && hits[0].distance < len - 24) {
        camera.position.copy(head).addScaledVector(toCam, Math.max(24, hits[0].distance - 12));
      }
    }
  }
}

export const ZOOM_MIN = 40;     // units — don't clip into walls
export const ZOOM_MAX = 16000;  // units — far overview

export function dollyFreeCamera(factor: number): void {
  const dir = tmpVec.set(-Math.sin(state.freeYaw), 0, -Math.cos(state.freeYaw));
  dir.y = Math.sin(state.freePitch);
  dir.normalize();
  const next = camera.position.clone().addScaledVector(dir, -factor);
  const dist = next.distanceTo(camera.position);
  if (dist > ZOOM_MAX * 0.5) return; // absurd jump guard
  camera.position.copy(next);
  camera.position.y = clamp(camera.position.y, ZOOM_MIN, ZOOM_MAX);
  state.zoomFlashUntil = performance.now() + 900;
  // NOTE: main.ts also called updateHud() here; that belongs to the wiring
  // layer (input/ui), so callers must refresh the HUD after dollying.
}

// Re-exported for typing convenience in sibling modules (no runtime value).
export type { CameraMode };

// Renderer, scene, camera, lights and the fixed object groups. Created once at
// import time (same order as the original main.ts).

import * as THREE from 'three';
import { viewport } from './dom';

export const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(viewport.clientWidth || 1, viewport.clientHeight || 1);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.15;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.domElement.tabIndex = 0;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
viewport.appendChild(renderer.domElement);

export const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0c141d);
scene.fog = new THREE.Fog(0x101a25, 3500, 19000);

export const camera = new THREE.PerspectiveCamera(
  72,
  (viewport.clientWidth || 1) / (viewport.clientHeight || 1),
  4,
  60000,
);
camera.rotation.order = 'YXZ';

export const hemi = new THREE.HemisphereLight(0x9fc5ff, 0x2a2118, 2.1);
scene.add(hemi);

export const sun = new THREE.DirectionalLight(0xfff0d2, 2.6);
sun.position.set(4200, 7000, 3400);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0005;
scene.add(sun);
scene.add(sun.target);

export const fill = new THREE.DirectionalLight(0x6f9cd6, 0.85);
fill.position.set(-5000, 2600, -4200);
scene.add(fill);

export const fallbackGrid = new THREE.GridHelper(24000, 48, 0x2c3d52, 0x18232f);
fallbackGrid.position.y = -1;
scene.add(fallbackGrid);

export const playerGroup = new THREE.Group();
scene.add(playerGroup);

export const projectileGroup = new THREE.Group();
scene.add(projectileGroup);

export const damageGroup = new THREE.Group();
scene.add(damageGroup);

export function resize(): void {
  const w = viewport.clientWidth || 1;
  const h = viewport.clientHeight || 1;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h, false);
}

export const resizeObserver = new ResizeObserver(resize);
resizeObserver.observe(viewport);
window.addEventListener('resize', resize);

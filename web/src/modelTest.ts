// Standalone test scene: renders one instance of every player model on a grid.
// Open at /modeltest.html. No replay, no map — only model loading and placement.
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

const CLASS_NAMES = [
  'scout', 'soldier', 'pyro', 'demoman', 'heavy',
  'engineer', 'medic', 'sniper', 'spy',
] as const;

const statusEl = document.getElementById('status') as HTMLDivElement;
const viewport = document.getElementById('viewport') as HTMLDivElement;

function log(msg: string): void {
  statusEl.textContent += msg + '\n';
  console.log(msg);
}

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
viewport.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a2230);

const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 1, 20000);
camera.position.set(300, 500, 900);
camera.lookAt(350, 60, 0);

const hemi = new THREE.HemisphereLight(0xffffff, 0x223311, 1.4);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(600, 1200, 400);
sun.castShadow = true;
scene.add(sun);

const grid = new THREE.GridHelper(6000, 60, 0x445566, 0x223344);
scene.add(grid);

const loader = new GLTFLoader();

async function main(): Promise<void> {
  let row = 0;
  for (const cls of CLASS_NAMES) {
    let col = 0;
    for (const team of ['red', 'blue'] as const) {
      const url = `/models/players/${cls}_${team}.glb`;
      try {
        const gltf = await loader.loadAsync(url);
        const obj = gltf.scene;
        const bbox = new THREE.Box3().setFromObject(obj);
        const size = bbox.getSize(new THREE.Vector3());
        const meshes: string[] = [];
        let tris = 0;
        obj.traverse((o) => {
          if (o instanceof THREE.Mesh) {
            o.castShadow = true;
            meshes.push(o.name || 'mesh');
            const g = o.geometry;
            tris += g.index ? g.index.count / 3 : g.getAttribute('position').count / 3;
          }
        });
        const x = col * 90;
        const z = row * 110;
        obj.position.set(x, 0, z);
        scene.add(obj);
        log(
          `${cls}_${team}: OK  size=(${size.x.toFixed(0)},${size.y.toFixed(0)},${size.z.toFixed(0)}) ` +
          `minY=${bbox.min.y.toFixed(1)} tris=${tris.toFixed(0)} meshes=[${meshes.join(',')}]`,
        );
      } catch (err) {
        log(`${cls}_${team}: FAIL ${String(err).slice(0, 120)}`);
      }
      col++;
    }
    row++;
  }
  log('--- done ---');
}

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// Simple orbit-ish drag to look around (desktop only, test page).
let dragging = false;
let lastX = 0;
let lastY = 0;
renderer.domElement.addEventListener('pointerdown', (e) => {
  dragging = true;
  lastX = e.clientX;
  lastY = e.clientY;
});
window.addEventListener('pointerup', () => (dragging = false));
window.addEventListener('pointermove', (e) => {
  if (!dragging) return;
  const dx = e.clientX - lastX;
  const dy = e.clientY - lastY;
  lastX = e.clientX;
  lastY = e.clientY;
  const pivot = new THREE.Vector3(350, 60, 0);
  const sph = camera.position.clone().sub(pivot);
  const r = new THREE.Spherical().setFromVector3(sph);
  r.theta -= dx * 0.005;
  r.phi = Math.max(0.15, Math.min(1.5, r.phi - dy * 0.005));
  camera.position.copy(pivot).add(new THREE.Vector3().setFromSpherical(r));
  camera.lookAt(pivot);
});

void main();

function animate(): void {
  requestAnimationFrame(animate);
  renderer.render(scene, camera);
}
animate();

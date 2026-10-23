// Skybox: loads the cube-face PNGs exported by tools/export_sky.py
// (sky.json manifest next to the map GLB) and sets scene.background.
// Failure of any part keeps the plain dark background — the sky is cosmetic.

import * as THREE from 'three';
import { t } from '../i18n';
import { scene } from './scene';

interface SkyManifest {
  sky?: string;
  size?: number;
  faces?: Record<string, string | undefined>;
  fog?: [number, number, number] | null;
}

// three.js CubeTextureLoader face order: +x, -x, +y, -y, +z, -z.
const SLOT_ORDER = ['px', 'nx', 'py', 'ny', 'pz', 'nz'] as const;

export async function loadSky(baseUrl: string): Promise<void> {
  const response = await fetch(`${baseUrl}sky.json`, { cache: 'no-cache' });
  if (!response.ok) {
    throw new Error(t('errFileNotFound', { url: `${baseUrl}sky.json` }));
  }
  const manifest = (await response.json()) as SkyManifest;
  const faces = manifest.faces ?? {};
  const urls = SLOT_ORDER.map((slot) => faces[slot]);
  if (urls.some((name) => !name)) {
    throw new Error(`sky.json is missing faces (${urls.length}/6 present)`);
  }

  const cube = await new THREE.CubeTextureLoader()
    .setPath(baseUrl)
    .loadAsync(urls as string[]);
  cube.colorSpace = THREE.SRGBColorSpace;
  cube.needsUpdate = true;
  scene.background = cube;

  // Distant geometry should fade into the horizon haze, not the old navy.
  if (manifest.fog && scene.fog instanceof THREE.Fog) {
    scene.fog.color.setRGB(
      manifest.fog[0] / 255,
      manifest.fog[1] / 255,
      manifest.fog[2] / 255,
    );
  }
}

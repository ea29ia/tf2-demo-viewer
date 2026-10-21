// Damage events: splash hitbox rings + floating damage numbers
// Extracted from main.ts (behavior-preserving port).

import * as THREE from 'three';
import { findFrameIndex } from '../replay';
import { state } from './state';
import { damageGroup } from './scene';

interface DamagePopup {
  sprite: THREE.Sprite;
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  texture: THREE.CanvasTexture;
  bornTick: number;
  crit: boolean;
  lastDamage: number;
}

interface SplashFx {
  mesh: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>;
  bornTick: number;
  radius: number;
}

const damagePopups: DamagePopup[] = [];
const splashFx: SplashFx[] = [];
const popupPool: DamagePopup[] = [];
const POPUP_LIFE_TICKS = 30; // ~0.45 s at 66 tick
const SPLASH_LIFE_TICKS = 14;

function createPopup(): DamagePopup {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 128;
  const ctx = canvas.getContext('2d')!;
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }),
  );
  sprite.visible = false;
  sprite.renderOrder = 10;
  damageGroup.add(sprite);
  return { sprite, canvas, ctx, texture, bornTick: 0, crit: false, lastDamage: 0 };
}

const DMG_WHITE = '#f2f6ff';
const DMG_CRIT = '#ffd24a';

function drawPopup(p: DamagePopup, damage: number, crit: boolean, alpha: number): void {
  const { canvas, ctx } = p;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const text = String(damage);
  const size = crit ? 76 : 58;
  ctx.font = `900 ${size}px Inter, Arial, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineJoin = 'round';
  ctx.lineWidth = 10;
  ctx.strokeStyle = 'rgba(0,0,0,0.85)';
  ctx.strokeText(text, canvas.width / 2, canvas.height / 2);
  ctx.fillStyle = crit ? DMG_CRIT : DMG_WHITE;
  ctx.globalAlpha = alpha;
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);
  ctx.globalAlpha = 1;
  p.texture.needsUpdate = true;
}

export function spawnDamage(dmg: {
  damage: number;
  crit: boolean;
  x: number;
  y: number;
  z: number;
  tick: number;
}): void {
  const popup = popupPool.pop() ?? createPopup();
  popup.bornTick = dmg.tick;
  popup.crit = dmg.crit;
  // Source (x, y, z) Z-up -> scene (x, z, -y)
  popup.sprite.position.set(dmg.x, dmg.z + 58 + Math.random() * 14, -dmg.y);
  popup.sprite.scale.set(64, 32, 1);
  popup.lastDamage = dmg.damage;
  popup.sprite.visible = true;
  drawPopup(popup, dmg.damage, dmg.crit, 1);
  damagePopups.push(popup);
}

export function spawnSplash(x: number, y: number, z: number, radius: number, tick: number): void {
  const r = Math.max(60, Math.min(radius || 146, 400));
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(r, 20, 14),
    new THREE.MeshBasicMaterial({
      color: 0xffa040,
      transparent: true,
      opacity: 0.22,
      depthWrite: false,
      side: THREE.DoubleSide,
    }),
  );
  mesh.position.set(x, z, -y);
  damageGroup.add(mesh);
  splashFx.push({ mesh, bornTick: tick, radius: r });
}

export function updateDamageFx(): void {
  if (!state.loaded) return;
  // Spawn new hurts at the current tick.
  const frames = state.loaded.replay.frames;
  const i = findFrameIndex(frames, state.playbackTick);
  const frame = frames[i];
  const prev = frames[Math.max(0, i - 1)];
  if (frame.hurts && frame !== prev) {
    for (const h of frame.hurts) {
      if (h.damage <= 0) continue;
      const crit = (h.flags & 1) !== 0;
      const isExplosion = h.splash > 0;
      spawnDamage({ damage: h.damage, crit, x: h.x, y: h.y, z: h.z, tick: frame.tick });
      if (isExplosion) spawnSplash(h.x, h.y, h.z, h.splash, frame.tick);
    }
  }

  // Age popups.
  for (let k = damagePopups.length - 1; k >= 0; k--) {
    const p = damagePopups[k];
    const age = state.playbackTick - p.bornTick;
    if (age < 0 || age > POPUP_LIFE_TICKS) {
      p.sprite.visible = false;
      popupPool.push(p);
      damagePopups.splice(k, 1);
      continue;
    }
    const t = age / POPUP_LIFE_TICKS;
    // float upward and fade out like in-game damage numbers
    p.sprite.position.y += 1.6;
    p.sprite.material.opacity = 1 - t * t;
    drawPopup(p, p.lastDamage ?? 0, p.crit, 1 - t * t);
  }

  // Fade splash spheres: slightly expand + fade.
  for (let k = splashFx.length - 1; k >= 0; k--) {
    const s = splashFx[k];
    const age = state.playbackTick - s.bornTick;
    if (age < 0 || age > SPLASH_LIFE_TICKS) {
      damageGroup.remove(s.mesh);
      s.mesh.geometry.dispose();
      s.mesh.material.dispose();
      splashFx.splice(k, 1);
      continue;
    }
    const t = age / SPLASH_LIFE_TICKS;
    s.mesh.scale.setScalar(1 + t * 0.25);
    s.mesh.material.opacity = 0.22 * (1 - t);
  }
}

// Name labels (canvas sprites) — extracted verbatim from main.ts.

import * as THREE from 'three';
import { classInfo, teamColor } from './tf2';

export interface NameLabel {
  sprite: THREE.Sprite;
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  texture: THREE.CanvasTexture;
  key: string;
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function truncate(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let out = text;
  while (out.length > 1 && ctx.measureText(`${out}…`).width > maxWidth) {
    out = out.slice(0, -1);
  }
  return `${out}…`;
}

export function createNameLabel(): NameLabel {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D context недоступен');

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;

  const material = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
  });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(168, 42, 1);
  sprite.renderOrder = 1000;

  return { sprite, canvas, ctx, texture, key: '' };
}

export function drawNameLabel(
  label: NameLabel,
  view: { name: string; team: number; cls: number; health: number; alive: boolean; entity: number },
): void {
  const key = `${view.name}|${view.team}|${view.cls}|${view.health}|${view.alive}`;
  if (key === label.key) return;
  label.key = key;

  const { ctx, canvas } = label;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const info = classInfo(view.cls);
  const accent = `#${teamColor(view.team).toString(16).padStart(6, '0')}`;

  roundRect(ctx, 6, 10, canvas.width - 12, canvas.height - 20, 18);
  ctx.fillStyle = 'rgba(7, 11, 16, 0.72)';
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.10)';
  ctx.stroke();

  ctx.fillStyle = accent;
  roundRect(ctx, 6, 10, 10, canvas.height - 20, 6);
  ctx.fill();

  ctx.textBaseline = 'middle';
  ctx.font = '600 38px Inter, Arial, sans-serif';
  ctx.fillStyle = view.alive ? '#f2f6fa' : '#8b98a8';
  const name = truncate(ctx, view.name || `#${view.entity}`, 340);
  ctx.fillText(name, 30, 46);

  ctx.font = '700 27px Inter, Arial, sans-serif';
  ctx.fillStyle = `#${info.color.toString(16).padStart(6, '0')}`;
  ctx.fillText(info.abbr, 30, 91);

  ctx.font = '600 27px Inter, Arial, sans-serif';
  ctx.fillStyle = view.alive ? '#d8e2ec' : '#77828f';
  const hp = `${view.health} HP`;
  const hpWidth = ctx.measureText(hp).width;
  ctx.fillText(hp, canvas.width - 28 - hpWidth, 91);

  label.texture.needsUpdate = true;
}

// Frame cache + pose sampling. Shared by playback, projectiles, damage FX and
// camera. Lives outside state.ts to keep the raw state store data-only.

import {
  findFrameIndex,
  recordMap,
  samplePoses,
  type PlayerRecord,
  type Pose,
  type ProjectileRecord,
  type ReplayFrame,
} from '../replay';
import { state } from './state';

export function getFrameMap(frame: ReplayFrame): Map<number, PlayerRecord> {
  if (!state.loaded) return new Map();
  let map = state.loaded.frameMaps.get(frame);
  if (!map) {
    map = recordMap(frame);
    state.loaded.frameMaps.set(frame, map);
  }
  return map;
}

export function getProjMap(frame: ReplayFrame): Map<number, ProjectileRecord> {
  if (!state.loaded) return new Map();
  let map = state.loaded.projMaps.get(frame);
  if (!map) {
    map = new Map();
    for (const p of frame.projectiles ?? []) map.set(p.entity, p);
    state.loaded.projMaps.set(frame, map);
  }
  return map;
}

export function posesAt(tick: number): Map<number, Pose> {
  if (!state.loaded || state.loaded.replay.frames.length === 0) return new Map();
  const frames = state.loaded.replay.frames;
  const i = findFrameIndex(frames, tick);
  const a = frames[i];
  const b = frames[Math.min(i + 1, frames.length - 1)];
  const mapA = getFrameMap(a);
  const mapB = getFrameMap(b);

  let alpha = 0;
  if (a !== b && b.tick > a.tick) alpha = clamp((tick - a.tick) / (b.tick - a.tick), 0, 1);
  return samplePoses(a, b, mapA, mapB, alpha);
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

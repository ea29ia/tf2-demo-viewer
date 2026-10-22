// Central mutable state for the viewer. All fields that used to be module-level
// globals in main.ts live here so modules stay acyclic: they may import `state`
// but state imports no viewer module (only three.js and ../replay types).

import * as THREE from 'three';
import type { Pose, Replay, ReplayFrame, ReplayKill, ReplayMeta, PlayerRecord, ProjectileRecord } from '../replay';

export type CameraMode = 'free' | 'follow' | 'pov';

export const BASE = import.meta.env.BASE_URL;
export const REPLAY_URL = `${BASE}replay/replay.bin`;
export const MAP_URL = `${BASE}maps/cp_dustbowl.glb`;

export interface LoadedReplay {
  replay: Replay;
  meta: ReplayMeta;
  tickRate: number;
  minTick: number;
  maxTick: number;
  frameMaps: WeakMap<ReplayFrame, Map<number, PlayerRecord>>;
  projMaps: WeakMap<ReplayFrame, Map<number, ProjectileRecord>>;
}

export interface ViewerState {
  // replay / playback
  loaded: LoadedReplay | null;
  playbackTick: number;
  playing: boolean;
  speed: number;
  lastPoses: Map<number, Pose>;
  metaUsers: Map<number, string>;
  kills: ReplayKill[];

  // selection / camera mode
  selectedEntity: number | null;
  cameraMode: CameraMode;

  // toggles
  hideDead: boolean;
  dimPvs: boolean;
  showModels: boolean;
  mapBounds: THREE.Box3 | null;

  // free-fly camera
  freeYaw: number;
  freePitch: number;
  lookDragging: boolean;
  lastPointerX: number;
  lastPointerY: number;
  keys: Set<string>;

  // touch input
  isCoarse: boolean;
  touchSpeedMult: number; // pinch-to-zoom → camera speed multiplier (0.4x..3x)
  followDistance: number; // follow-mode camera distance behind the player
  followSnap: boolean; // snap immediately on mode/player change instead of lerping
  speedFlashUntil: number; // HUD flash timestamp for the speed multiplier
  zoomFlashUntil: number; // HUD flash timestamp for zoom/follow distance
  joyForward: number; // virtual joystick axes (-1..1), 0 on desktop
  joySide: number;
  elevAxis: number; // elevation slider (Q/E equivalent)

  // UI render throttling
  lastListRender: number;
  lastListSignature: string;
  lastKillSignature: string;
  killItems: HTMLElement[];

  // map geometry (filled by loadMap; used by camera collision + inspect)
  mapMeshes: THREE.Mesh[];
  foliageMeshes: THREE.Mesh[];
}

export const state: ViewerState = {
  loaded: null,
  playbackTick: 0,
  playing: false,
  speed: 1,
  lastPoses: new Map(),

  metaUsers: new Map(),
  kills: [],

  selectedEntity: null,
  cameraMode: 'free',

  hideDead: false,
  dimPvs: true,
  showModels: true,
  mapBounds: null,

  freeYaw: 0,
  freePitch: 0,
  lookDragging: false,
  lastPointerX: 0,
  lastPointerY: 0,
  keys: new Set<string>(),

  isCoarse:
    typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches,
  touchSpeedMult: 1,
  followDistance: 265,
  followSnap: false,
  speedFlashUntil: 0,
  zoomFlashUntil: 0,
  joyForward: 0,
  joySide: 0,
  elevAxis: 0,

  lastListRender: 0,
  lastListSignature: '',
  lastKillSignature: '',
  killItems: [],

  mapMeshes: [],
  foliageMeshes: [],
};

/**
 * Parser for the TF2 demo browser replay format.
 *
 * Layout (little endian):
 *   magic        4 bytes  "TF2R"
 *   version      u32      = 1
 *   meta_len     u32
 *   meta         meta_len bytes UTF-8 JSON
 *   frame_count  u32
 *   frames[frame_count]:
 *     tick       u32
 *     count      u16
 *     count * 26-byte records:
 *       entity   u16
 *       team     u8     2=red, 3=blue
 *       class    u8     1..9 (scout..engineer), 0=other
 *       flags    u8     bit0 alive, bit1 in_pvs
 *       health   u8
 *       x,y,z    f32 x3 Source units
 *       yaw      f32    degrees
 *       pitch    f32    degrees
 */

export const RECORD_SIZE = 26;
export const PROJ_RECORD_SIZE = 17;
export const HURT_RECORD_SIZE = 15;
export const MAGIC = 'TF2R';
export const VERSION = 2;

export type TeamName = 'red' | 'blue' | string;

export interface ReplayUser {
  entity: number;
  name: string;
  userId?: number;
  steamId?: string;
  team: TeamName;
}

export interface ReplayKill {
  tick: number;
  attacker: number;
  victim: number;
  assister?: number | null;
  weapon: string;
}

export interface ReplayMeta {
  map: string;
  nick: string;
  duration: number;
  ticks: number;
  frames: number;
  intervalPerTick: number;
  tickRate: number;
  startTick: number;
  users: ReplayUser[];
  kills: ReplayKill[];
}

export interface PlayerRecord {
  entity: number;
  team: number;
  cls: number;
  flags: number;
  health: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  pitch: number;
}

export interface ProjectileRecord {
  entity: number;
  team: number;
  kind: number; // 0 rocket, 1 arrow, 2 sticky, 3 pipe, 4 flare, 5 loose cannon, 7 unknown
  flags: number; // bit0 critical
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  vz: number;
}

export interface HurtRecord {
  victim: number;
  attacker: number;
  damage: number;
  flags: number; // bit0 crit, bit1 mini-crit, bit2 fake-crit
  x: number;
  y: number;
  z: number;
  splash: number;
}

export interface ReplayFrame {
  tick: number;
  records: PlayerRecord[];
  projectiles: ProjectileRecord[];
  hurts: HurtRecord[];
}

export interface Replay {
  meta: ReplayMeta;
  frames: ReplayFrame[];
}

const decoder = new TextDecoder('utf-8');

function readMagic(view: DataView, offset: number): string {
  let out = '';
  for (let i = 0; i < 4; i++) out += String.fromCharCode(view.getUint8(offset + i));
  return out;
}

/** Parse a replay.bin ArrayBuffer, throwing readable errors on malformed input. */
export function parseReplay(buffer: ArrayBuffer): Replay {
  const view = new DataView(buffer);
  const total = buffer.byteLength;
  let offset = 0;

  if (total < 16) {
    throw new Error(`replay.bin слишком короткий: ${total} байт (минимум 16)`);
  }

  const magic = readMagic(view, offset);
  offset += 4;
  if (magic !== MAGIC) {
    throw new Error(`Неверная сигнатура replay.bin: "${magic}" (ожидается "${MAGIC}")`);
  }

  const version = view.getUint32(offset, true);
  offset += 4;
  if (version !== VERSION) {
    throw new Error(`Неподдерживаемая версия replay.bin: ${version} (ожидается ${VERSION})`);
  }

  const metaLen = view.getUint32(offset, true);
  offset += 4;
  if (metaLen > total - offset) {
    throw new Error(
      `Повреждён replay.bin: meta_len=${metaLen} выходит за границу файла (осталось ${total - offset} байт)`,
    );
  }

  let metaRaw: string;
  try {
    metaRaw = decoder.decode(new Uint8Array(buffer, offset, metaLen));
  } catch {
    throw new Error('Не удалось декодировать meta JSON из replay.bin (UTF-8)');
  }
  offset += metaLen;

  let meta: ReplayMeta;
  try {
    meta = JSON.parse(metaRaw) as ReplayMeta;
  } catch (err) {
    throw new Error(`Некорректный meta JSON внутри replay.bin: ${(err as Error).message}`);
  }
  if (!meta || typeof meta !== 'object') {
    throw new Error('meta внутри replay.bin не является объектом');
  }
  meta.users = Array.isArray(meta.users) ? meta.users : [];
  meta.kills = Array.isArray(meta.kills) ? meta.kills : [];

  if (offset + 4 > total) {
    throw new Error('Повреждён replay.bin: отсутствует frame_count');
  }
  const frameCount = view.getUint32(offset, true);
  offset += 4;

  if (frameCount > 10_000_000) {
    throw new Error(`Подозрительный frame_count=${frameCount} в replay.bin`);
  }

  const frames: ReplayFrame[] = new Array(frameCount);
  for (let f = 0; f < frameCount; f++) {
    if (offset + 6 > total) {
      throw new Error(`Повреждён replay.bin: обрезан заголовок кадра #${f} (offset ${offset}/${total})`);
    }
    const tick = view.getUint32(offset, true);
    const count = view.getUint16(offset + 4, true);
    offset += 6;

    const bytes = count * RECORD_SIZE;
    if (offset + bytes > total) {
      throw new Error(
        `Повреждён replay.bin: кадр #${f} (tick ${tick}) требует ${bytes} байт, осталось ${total - offset}`,
      );
    }

    const records: PlayerRecord[] = new Array(count);
    for (let i = 0; i < count; i++) {
      records[i] = {
        entity: view.getUint16(offset, true),
        team: view.getUint8(offset + 2),
        cls: view.getUint8(offset + 3),
        flags: view.getUint8(offset + 4),
        health: view.getUint8(offset + 5),
        x: view.getFloat32(offset + 6, true),
        y: view.getFloat32(offset + 10, true),
        z: view.getFloat32(offset + 14, true),
        yaw: view.getFloat32(offset + 18, true),
        pitch: view.getFloat32(offset + 22, true),
      };
      offset += RECORD_SIZE;
    }

    // v2+: projectiles block after player records.
    let projectiles: ProjectileRecord[] = [];
    if (version >= 2) {
      if (offset + 2 > total) {
        throw new Error(`Повреждён replay.bin: обрезан блок снарядов кадра #${f}`);
      }
      const projCount = view.getUint16(offset, true);
      offset += 2;
      const pbytes = projCount * PROJ_RECORD_SIZE;
      if (offset + pbytes > total) {
        throw new Error(
          `Повреждён replay.bin: снаряды кадра #${f} требуют ${pbytes} байт, осталось ${total - offset}`,
        );
      }
      projectiles = new Array(projCount);
      for (let i = 0; i < projCount; i++) {
        const q = (o: number): number => view.getInt16(offset + o, true) * 4;
        projectiles[i] = {
          entity: view.getUint16(offset, true),
          team: view.getUint8(offset + 2),
          kind: view.getUint8(offset + 3),
          flags: view.getUint8(offset + 4),
          x: q(5),
          y: q(7),
          z: q(9),
          vx: q(11),
          vy: q(13),
          vz: q(15),
        };
        offset += PROJ_RECORD_SIZE;
      }
    }

    // v2+: player_hurt (damage) block after projectiles.
    let hurts: HurtRecord[] = [];
    if (version >= 2) {
      if (offset + 2 > total) {
        throw new Error(`Повреждён replay.bin: обрезан блок урона кадра #${f}`);
      }
      const hurtCount = view.getUint16(offset, true);
      offset += 2;
      const hbytes = hurtCount * HURT_RECORD_SIZE;
      if (offset + hbytes > total) {
        throw new Error(
          `Повреждён replay.bin: урон кадра #${f} требует ${hbytes} байт, осталось ${total - offset}`,
        );
      }
      hurts = new Array(hurtCount);
      for (let i = 0; i < hurtCount; i++) {
        const q = (o: number): number => view.getInt16(offset + o, true) * 4;
        hurts[i] = {
          victim: view.getUint16(offset, true),
          attacker: view.getUint16(offset + 2, true),
          damage: view.getUint16(offset + 4, true),
          flags: view.getUint8(offset + 6),
          x: q(7),
          y: q(9),
          z: q(11),
          splash: q(13),
        };
        offset += HURT_RECORD_SIZE;
      }
    }

    frames[f] = { tick, records, projectiles, hurts };
  }

  // Be tolerant of a producer that writes frames in a different order.
  let sorted = true;
  for (let i = 1; i < frames.length; i++) {
    if (frames[i].tick < frames[i - 1].tick) {
      sorted = false;
      break;
    }
  }
  if (!sorted) frames.sort((a, b) => a.tick - b.tick);

  return { meta, frames };
}

/** Binary-search the last frame whose tick is <= the requested tick. */
export function findFrameIndex(frames: ReplayFrame[], tick: number): number {
  if (frames.length === 0) return -1;
  if (tick <= frames[0].tick) return 0;
  const last = frames.length - 1;
  if (tick >= frames[last].tick) return last;

  let lo = 0;
  let hi = last;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (frames[mid].tick <= tick) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** Record lookup map for one frame (built once, then cached by the caller). */
export function recordMap(frame: ReplayFrame): Map<number, PlayerRecord> {
  const map = new Map<number, PlayerRecord>();
  for (const rec of frame.records) map.set(rec.entity, rec);
  return map;
}

export interface Pose {
  entity: number;
  team: number;
  cls: number;
  flags: number;
  health: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  pitch: number;
  interpolated: boolean;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function lerpAngleDeg(a: number, b: number, t: number): number {
  let delta = ((b - a + 540) % 360) - 180;
  if (delta === -180) delta = 180;
  return a + delta * t;
}

/**
 * Build interpolated poses for all entities visible in either surrounding frame.
 * Teleports (> 512 Source units between frames) snap to the newer pose.
 */
export function samplePoses(
  framesA: ReplayFrame,
  framesB: ReplayFrame,
  mapA: Map<number, PlayerRecord>,
  mapB: Map<number, PlayerRecord>,
  alpha: number,
): Map<number, Pose> {
  const poses = new Map<number, Pose>();

  for (const a of mapA.values()) {
    const b = mapB.get(a.entity);
    const t = b ? alpha : 0;
    const target = b ?? a;

    const dx = target.x - a.x;
    const dy = target.y - a.y;
    const dz = target.z - a.z;
    const teleported = b !== undefined && Math.hypot(dx, dy, dz) > 512;

    const mix = teleported ? 1 : t;
    const flags = mix < 0.5 ? a.flags : target.flags;
    const team = mix < 0.5 ? a.team : target.team;
    const cls = mix < 0.5 ? a.cls : target.cls;

    poses.set(a.entity, {
      entity: a.entity,
      team,
      cls,
      flags,
      health: Math.round(lerp(a.health, target.health, mix)),
      x: teleported ? target.x : lerp(a.x, target.x, mix),
      y: teleported ? target.y : lerp(a.y, target.y, mix),
      z: teleported ? target.z : lerp(a.z, target.z, mix),
      yaw: teleported ? target.yaw : lerpAngleDeg(a.yaw, target.yaw, mix),
      pitch: teleported ? target.pitch : lerp(a.pitch, target.pitch, mix),
      interpolated: b !== undefined && !teleported,
    });
  }

  // Entities that only exist in the newer frame appear immediately.
  for (const b of mapB.values()) {
    if (mapA.has(b.entity)) continue;
    poses.set(b.entity, {
      entity: b.entity,
      team: b.team,
      cls: b.cls,
      flags: b.flags,
      health: b.health,
      x: b.x,
      y: b.y,
      z: b.z,
      yaw: b.yaw,
      pitch: b.pitch,
      interpolated: false,
    });
  }

  return poses;
}

export const FLAG_ALIVE = 1 << 0;
export const FLAG_IN_PVS = 1 << 1;

export function isAlive(flags: number): boolean {
  return (flags & FLAG_ALIVE) !== 0;
}

export function isInPvs(flags: number): boolean {
  return (flags & FLAG_IN_PVS) !== 0;
}

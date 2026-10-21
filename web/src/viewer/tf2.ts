// TF2 domain constants and small pure helpers. No mutable state, no imports
// except i18n (for nothing currently — kept dependency-free on purpose).

export const TEAM_RED = 2;
export const CLASS_SPY = 8;
export const TEAM_BLUE = 3;

export const TEAM_COLOR: Record<number, number> = {
  [TEAM_RED]: 0xe03c3c,
  [TEAM_BLUE]: 0x2f7bff,
};

export interface ClassInfo {
  abbr: string;
  name: string;
  color: number;
}

export const OTHER_CLASS: ClassInfo = { abbr: 'OTH', name: 'Other', color: 0x9aa7b8 };

export const CLASSES: Record<number, ClassInfo> = {
  1: { abbr: 'SCO', name: 'Scout', color: 0x8fd14f },
  2: { abbr: 'SOL', name: 'Soldier', color: 0x6f8cff },
  3: { abbr: 'PYR', name: 'Pyro', color: 0xff8b3d },
  4: { abbr: 'DEM', name: 'Demoman', color: 0xc07bd8 },
  5: { abbr: 'HEV', name: 'Heavy', color: 0xe0b04a },
  6: { abbr: 'ENG', name: 'Engineer', color: 0xf2d05a },
  7: { abbr: 'MED', name: 'Medic', color: 0x57d6c4 },
  8: { abbr: 'SNI', name: 'Sniper', color: 0xa7c46a },
  9: { abbr: 'SPY', name: 'Spy', color: 0xd9d9e3 },
};

export const CAPSULE_RADIUS = 16;
export const CAPSULE_LENGTH = 40; // total height 72 with caps
export const CAPSULE_CENTER_Y = CAPSULE_RADIUS + CAPSULE_LENGTH / 2;
export const EYE_HEIGHT = 64;

export function classInfo(cls: number): ClassInfo {
  return CLASSES[cls] ?? OTHER_CLASS;
}

export function teamColor(team: number): number {
  return TEAM_COLOR[team] ?? 0x9aa7b8;
}

export function teamClass(team: number): string {
  if (team === TEAM_RED) return 'red';
  if (team === TEAM_BLUE) return 'blue';
  return 'other';
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

export function fmtTime(sec: number): string {
  const safe = Math.max(0, sec);
  const m = Math.floor(safe / 60);
  const s = safe - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

export function lerpN(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

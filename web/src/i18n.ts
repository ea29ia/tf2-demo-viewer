/**
 * Minimal i18n for the viewer UI (RU / EN).
 *
 * Static DOM text is tagged with `data-i18n` (textContent) and
 * `data-i18n-title` (title attribute) attributes in index.html and applied by
 * applyLang(). Dynamic strings are produced via t() at render time; they are
 * refreshed on the next tick or the next forced re-render after a language
 * switch.
 */

export type Lang = 'ru' | 'en';

const STRINGS: Record<Lang, Record<string, string>> = {
  ru: {
    // HUD
    hudCamera: 'Камера: {mode}',
    hudDistance: 'Дистанция: {n}',
    hudZoom: 'Zoom: {n} юнитов',
    hudSpeed: 'Скорость камеры: {n}',
    hudOutOfFrame: 'Игрок #{n} вне кадра на этом тике',
    // playback
    statRedWord: 'красных',
    statBlueWord: 'синих',
    statDeadWord: 'мёртвых',
    speedTitle: 'Скорость воспроизведения',
    freeBtnTitle: 'Сбросить свободную камеру (Esc)',
    // players panel
    playersTitle: 'Игроки',
    pNotSeen: 'нет в кадре',
    pDead: 'мёртв',
    pOutOfPvs: 'вне PVS',
    playersEmpty: 'Нет игроков в кадре',
    // killfeed
    killfeedTitle: 'Killfeed',
    killfeedSub: 'последние 8',
    killfeedEmpty: 'Пока тихо…',
    // toggles
    toggleHideDead: 'Скрывать мёртвых',
    toggleDimPvs: 'Приглушать вне PVS',
    toggleHideFoliage: 'Скрывать кусты/траву',
    // footer hints
    hintDesktop:
      'WASD + Q/E — полёт · ПКМ — обзор · Space — пауза · Esc — Free · F / V — follow / POV',
    hintTouch:
      'Один палец — обзор · Джойстик — движение · Правый ползунок — верх/низ · Щипок — скорость',
    // overlay
    overlayTitle: 'Не хватает файлов',
    overlayLead: 'положи replay.bin и glb в public/...',
    overlayReload: 'Повторить',
    overlayClose: 'Закрыть',
    errFileNotFound: '{url}: файл не найден (сервер вернул HTML)',
    errMap: 'Карта: {msg}',
    errReplay: 'Реплей: {msg}',
    errNoFrames: 'replay.bin не содержит кадров',
    loadDotTitle: 'Состояние загрузки',
    sheetAria: 'Развернуть панель',
    // replay.ts parse errors
    errShortBin: 'replay.bin слишком короткий: {n} байт (минимум 16)',
    errBadMagic: 'Неверная сигнатура replay.bin: "{got}" (ожидается "{want}")',
    errBadVersion: 'Неподдерживаемая версия replay.bin: {got} (ожидается {want})',
    errMetaOverrun:
      'Повреждён replay.bin: meta_len={n} выходит за границу файла (осталось {left} байт)',
    errMetaDecode: 'Не удалось декодировать meta JSON из replay.bin (UTF-8)',
    errMetaJson: 'Некорректный meta JSON внутри replay.bin: {msg}',
    errMetaNotObject: 'meta внутри replay.bin не является объектом',
    errNoFrameCount: 'Повреждён replay.bin: отсутствует frame_count',
    errBadFrameCount: 'Подозрительный frame_count={n} в replay.bin',
    errFrameHeaderTruncated:
      'Повреждён replay.bin: обрезан заголовок кадра #{n} (offset {offset}/{total})',
    errFrameDataTruncated:
      'Повреждён replay.bin: кадр #{n} (tick {tick}) требует {need} байт, осталось {left}',
    errProjBlockTruncated: 'Повреждён replay.bin: обрезан блок снарядов кадра #{n}',
    errProjDataTruncated:
      'Повреждён replay.bin: снаряды кадра #{n} требуют {need} байт, осталось {left}',
    errHurtBlockTruncated: 'Повреждён replay.bin: обрезан блок урона кадра #{n}',
    errHurtDataTruncated:
      'Повреждён replay.bin: урон кадра #{n} требует {need} байт, осталось {left}',
    // projectile labels
    projRocket: 'Ракета',
    projArrow: 'Стрела',
    projSticky: 'Липучка',
    projBomb: 'Бомба',
    projFlare: 'Флейр',
    projShell: 'Снаряд',
    // inspect card
    inspectPos: 'позиция: {x}, {y}, {z}',
    inspectCloseAria: 'Закрыть',
    propHealthkit: 'Аптечка',
    propAmmo: 'Патроны',
    propTrack: 'рельсы вагонетки',
    propHandrail: 'перила',
    propRock: 'камень',
    propBarrel: 'бочка',
    propCrate: 'ящик',
    propFence: 'забор',
    propShrub: 'куст',
    propLight: 'свет',
    propTree: 'дерево',
    propDefault: 'проп',
  },
  en: {
    // HUD
    hudCamera: 'Camera: {mode}',
    hudDistance: 'Distance: {n}',
    hudZoom: 'Zoom: {n} units',
    hudSpeed: 'Camera speed: {n}',
    hudOutOfFrame: 'Player #{n} is out of frame at this tick',
    // playback
    statRedWord: 'red',
    statBlueWord: 'blue',
    statDeadWord: 'dead',
    speedTitle: 'Playback speed',
    freeBtnTitle: 'Reset free camera (Esc)',
    // players panel
    playersTitle: 'Players',
    pNotSeen: 'not in frame',
    pDead: 'dead',
    pOutOfPvs: 'outside PVS',
    playersEmpty: 'No players in frame',
    // killfeed
    killfeedTitle: 'Killfeed',
    killfeedSub: 'last 8',
    killfeedEmpty: 'All quiet so far…',
    // toggles
    toggleHideDead: 'Hide dead',
    toggleDimPvs: 'Dim outside PVS',
    toggleHideFoliage: 'Hide shrubs/grass',
    // footer hints
    hintDesktop:
      'WASD + Q/E — fly · RMB — look · Space — pause · Esc — Free · F / V — follow / POV',
    hintTouch:
      'One finger — look · Joystick — move · Right slider — up/down · Pinch — speed',
    // overlay
    overlayTitle: 'Missing files',
    overlayLead: 'put replay.bin and glb into public/...',
    overlayReload: 'Retry',
    overlayClose: 'Close',
    errFileNotFound: '{url}: file not found (server returned HTML)',
    errMap: 'Map: {msg}',
    errReplay: 'Replay: {msg}',
    errNoFrames: 'replay.bin contains no frames',
    loadDotTitle: 'Loading state',
    sheetAria: 'Expand panel',
    // replay.ts parse errors
    errShortBin: 'replay.bin is too short: {n} bytes (min 16)',
    errBadMagic: 'Invalid replay.bin signature: "{got}" (expected "{want}")',
    errBadVersion: 'Unsupported replay.bin version: {got} (expected {want})',
    errMetaOverrun:
      'Corrupted replay.bin: meta_len={n} goes beyond the file (only {left} bytes left)',
    errMetaDecode: 'Failed to decode meta JSON from replay.bin (UTF-8)',
    errMetaJson: 'Invalid meta JSON inside replay.bin: {msg}',
    errMetaNotObject: 'meta inside replay.bin is not an object',
    errNoFrameCount: 'Corrupted replay.bin: frame_count is missing',
    errBadFrameCount: 'Suspicious frame_count={n} in replay.bin',
    errFrameHeaderTruncated:
      'Corrupted replay.bin: frame #{n} header is truncated (offset {offset}/{total})',
    errFrameDataTruncated:
      'Corrupted replay.bin: frame #{n} (tick {tick}) needs {need} bytes, {left} left',
    errProjBlockTruncated: 'Corrupted replay.bin: projectile block of frame #{n} is truncated',
    errProjDataTruncated:
      'Corrupted replay.bin: projectiles of frame #{n} need {need} bytes, {left} left',
    errHurtBlockTruncated: 'Corrupted replay.bin: hurt block of frame #{n} is truncated',
    errHurtDataTruncated:
      'Corrupted replay.bin: hurts of frame #{n} need {need} bytes, {left} left',
    // projectile labels
    projRocket: 'Rocket',
    projArrow: 'Arrow',
    projSticky: 'Sticky',
    projBomb: 'Pipe bomb',
    projFlare: 'Flare',
    projShell: 'Shell',
    // inspect card
    inspectPos: 'position: {x}, {y}, {z}',
    inspectCloseAria: 'Close',
    propHealthkit: 'Health kit',
    propAmmo: 'Ammo',
    propTrack: 'cart track',
    propHandrail: 'handrail',
    propRock: 'rock',
    propBarrel: 'barrel',
    propCrate: 'crate',
    propFence: 'fence',
    propShrub: 'shrub',
    propLight: 'light',
    propTree: 'tree',
    propDefault: 'prop',
  },
};

const STORAGE_KEY = 'tf2viewer.lang';

function detectLang(): Lang {
  const nav = typeof navigator !== 'undefined' ? navigator.language : '';
  return nav && nav.toLowerCase().startsWith('ru') ? 'ru' : 'en';
}

let current: Lang = detectLang();

function stored(): Lang | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw === 'ru' || raw === 'en' ? raw : null;
  } catch {
    return null;
  }
}

current = stored() ?? current;

export function getLang(): Lang {
  return current;
}

/** Translate `key` for the current language, substituting `{name}` params. */
export function t(key: string, params?: Record<string, string | number>): string {
  let text = STRINGS[current][key] ?? STRINGS.en[key] ?? key;
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      text = text.replaceAll(`{${name}}`, String(value));
    }
  }
  return text;
}

/** Apply the current language to all static DOM strings. */
export function applyLang(): void {
  document.documentElement.lang = current;
  for (const node of document.querySelectorAll<HTMLElement>('[data-i18n]')) {
    const key = node.dataset.i18n;
    if (key) node.textContent = t(key);
  }
  for (const node of document.querySelectorAll<HTMLElement>('[data-i18n-title]')) {
    const key = node.dataset.i18nTitle;
    if (key) node.setAttribute('title', t(key));
  }
  for (const node of document.querySelectorAll<HTMLElement>('[data-i18n-aria]')) {
    const key = node.dataset.i18nAria;
    if (key) node.setAttribute('aria-label', t(key));
  }
  document
    .querySelectorAll<HTMLButtonElement>('#langToggle [data-lang]')
    .forEach((btn) => btn.classList.toggle('active', btn.dataset.lang === current));
}

/** Switch language, persist the choice and re-apply static DOM strings. */
export function setLang(lang: Lang): void {
  current = lang;
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // private mode etc. — keep the choice for this session only
  }
  applyLang();
}

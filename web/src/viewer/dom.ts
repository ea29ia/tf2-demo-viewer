// DOM element lookups. Single place that knows the index.html element ids.

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Не найден DOM-элемент #${id}`);
  return node as T;
}

export const viewport = el<HTMLDivElement>('viewport');
export const sidebar = el<HTMLElement>('sidebar');
export const timeline = el<HTMLInputElement>('timeline');
export const timeNow = el<HTMLSpanElement>('timeNow');
export const timeTotal = el<HTMLSpanElement>('timeTotal');
export const playBtn = el<HTMLButtonElement>('playBtn');
export const speedSelect = el<HTMLSelectElement>('speed');
export const freeBtn = el<HTMLButtonElement>('freeBtn');
export const playersEl = el<HTMLDivElement>('players');
export const playerCount = el<HTMLSpanElement>('playerCount');
export const killfeedEl = el<HTMLUListElement>('killfeed');
export const hideDeadInput = el<HTMLInputElement>('hideDead');
export const dimPvsInput = el<HTMLInputElement>('dimPvs');
export const showModelsInput = el<HTMLInputElement>('showModels');
export const hideFoliageInput = el<HTMLInputElement>('hideFoliage');
export const wireframeInput = el<HTMLInputElement>('wireframe');
export const propBoxesInput = el<HTMLInputElement>('propBoxes');
export const mapNameEl = el<HTMLDivElement>('mapName');
export const hud = el<HTMLDivElement>('hud');
export const overlay = el<HTMLDivElement>('overlay');
export const overlayDetails = el<HTMLParagraphElement>('overlayDetails');
export const reloadBtn = el<HTMLButtonElement>('reloadBtn');
export const dismissBtn = el<HTMLButtonElement>('dismissBtn');
export const loadDot = el<HTMLDivElement>('loadDot');
export const demoFileInput = el<HTMLInputElement>('demoFile');
export const demoFileBtn = el<HTMLLabelElement>('demoFileBtn');
export const aliveRed = el<HTMLElement>('aliveRed');
export const aliveBlue = el<HTMLElement>('aliveBlue');
export const deadCount = el<HTMLElement>('deadCount');
export const inspectCard = el<HTMLDivElement>('inspectCard');
export const inspectTitle = el<HTMLSpanElement>('inspectTitle');
export const inspectLabel = el<HTMLDivElement>('inspectLabel');
export const inspectMeta = el<HTMLDivElement>('inspectMeta');
export const inspectClose = el<HTMLButtonElement>('inspectClose');

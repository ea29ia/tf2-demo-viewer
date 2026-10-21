// UI rendering + UI-facing actions extracted from main.ts (behavior-preserving
// port; main.ts remains the source of truth until the wiring is switched over).
//
// This module does NOT register playBtn/speedSelect/timeline/checkbox/freeBtn/
// mode-btn/playersEl listeners — playback.ts owns those. The '#langToggle'
// language-switcher wiring lives here.

import {
  aliveRed,
  aliveBlue,
  deadCount,
  hud,
  killfeedEl,
  playBtn,
  playerCount,
  playersEl,
  timeNow,
  timeTotal,
} from './dom';
import { classInfo, teamClass, fmtTime, TEAM_RED, TEAM_BLUE } from './tf2';
import { state, type CameraMode } from './state';
import { views } from './playerViews';
import { camera } from './scene';
import { FLAG_ALIVE } from '../replay';
import { t, getLang, setLang } from '../i18n';

// ---------------------------------------------------------------------------
// Timeline / stats
// ---------------------------------------------------------------------------

function currentSeconds(): number {
  const loaded = state.loaded;
  if (!loaded) return 0;
  return (state.playbackTick - loaded.minTick) / loaded.tickRate;
}

export function updateTimelineUi(): void {
  const loaded = state.loaded;
  if (!loaded) {
    timeNow.textContent = '0:00.0';
    timeTotal.textContent = '0:00.0';
    return;
  }
  const remain = (loaded.maxTick - state.playbackTick) / loaded.tickRate;
  timeNow.textContent = fmtTime(currentSeconds());
  timeTotal.textContent = `-${fmtTime(Math.max(0, remain))}`;
  updateAliveStats();
}

function updateAliveStats(): void {
  const loaded = state.loaded;
  if (!loaded) return;
  let red = 0;
  let blue = 0;
  let dead = 0;
  for (const pose of state.lastPoses.values()) {
    if ((pose.flags & FLAG_ALIVE) !== 0) {
      if (pose.team === TEAM_RED) red++;
      else if (pose.team === TEAM_BLUE) blue++;
    } else if (pose.team === TEAM_RED || pose.team === TEAM_BLUE) {
      dead++;
    }
  }
  aliveRed.textContent = String(red);
  aliveBlue.textContent = String(blue);
  deadCount.textContent = `${dead} ${t('statDeadWord')}`;
}

// ---------------------------------------------------------------------------
// Playback actions (called from input.ts hooks and playback.ts)
// ---------------------------------------------------------------------------

export function setPlaying(value: boolean): void {
  state.playing = value;
  playBtn.textContent = state.playing ? '❚❚' : '▶';
  playBtn.setAttribute('aria-label', state.playing ? 'Pause' : 'Play');
}

// Pure play/pause flip. The restart-from-end behavior (rewind when pressing
// play on the final tick) lives in playback.ts's togglePlayOrRestart, which is
// what the play button and input hooks actually call.
export function togglePlay(): void {
  setPlaying(!state.playing);
}

export function selectPlayer(entity: number, mode: CameraMode = 'follow'): void {
  if (state.selectedEntity !== entity || state.cameraMode !== mode) state.followSnap = true;
  state.selectedEntity = entity;
  state.cameraMode = mode;
  updateModeButtons();
  renderPlayers(true);
  updateHud();
}

export function setMode(mode: CameraMode): void {
  if (mode !== 'free' && state.selectedEntity === null) {
    // Prefer the demo recorder (meta.nick), else first alive player.
    const nick = state.loaded?.meta.nick;
    let fallback: number | null = null;
    if (nick) {
      for (const [entity, name] of state.metaUsers) {
        if (name === nick) {
          fallback = entity;
          break;
        }
      }
    }
    if (fallback === null) {
      for (const pose of state.lastPoses.values()) {
        if ((pose.flags & FLAG_ALIVE) !== 0) {
          fallback = pose.entity;
          break;
        }
      }
    }
    if (fallback === null && views.size > 0) fallback = views.keys().next().value ?? null;
    if (fallback === null) return;
    state.selectedEntity = fallback;
  }
  if (state.cameraMode !== mode) state.followSnap = mode !== 'free';
  state.cameraMode = mode;
  updateModeButtons();
  renderPlayers(true);
  updateHud();
}

export function updateModeButtons(): void {
  for (const btn of Array.from(document.querySelectorAll<HTMLButtonElement>('.mode-btn'))) {
    btn.classList.toggle('active', btn.dataset.mode === state.cameraMode);
  }
}

export function updateHud(): void {
  if (performance.now() < state.zoomFlashUntil) {
    if (state.cameraMode === 'follow') {
      hud.textContent = t('hudDistance', { n: Math.round(state.followDistance) });
    } else {
      const dist = Math.max(1, Math.round(camera.position.length()));
      hud.textContent = t('hudZoom', { n: dist });
    }
    return;
  }
  if (performance.now() < state.speedFlashUntil) {
    hud.textContent = t('hudSpeed', { n: state.touchSpeedMult.toFixed(1) });
    return;
  }
  const modeLabel =
    state.cameraMode === 'free' ? 'Free' : state.cameraMode === 'follow' ? 'Follow' : 'POV';
  let text = t('hudCamera', { mode: modeLabel });
  if (state.cameraMode !== 'free' && state.selectedEntity !== null) {
    const view = views.get(state.selectedEntity);
    text += ` · ${view?.name ?? `#${state.selectedEntity}`}`;
  }
  if (state.loaded) {
    text += `  ·  tick ${Math.round(state.playbackTick)}`;
  }
  hud.textContent = text;
}

// ---------------------------------------------------------------------------
// Side panel
// ---------------------------------------------------------------------------

export function renderPlayers(force = false): void {
  const loaded = state.loaded;
  if (!loaded) return;
  const now = performance.now();
  if (!force && now - state.lastListRender < 180) return;

  const rows = Array.from(views.values())
    .filter((v) => v.entity !== 0 && !(state.hideDead && v.seen && !v.alive))
    .sort((a, b) => {
      const ta = a.team || 9;
      const tb = b.team || 9;
      if (ta !== tb) return ta - tb;
      return (a.name || '').localeCompare(b.name || '', 'ru');
    });

  const signature = rows
    .map(
      (v) =>
        `${v.entity}:${v.team}:${v.cls}:${v.health}:${v.alive ? 1 : 0}:${v.inPvs ? 1 : 0}:${
          v.seen ? 1 : 0
        }:${state.selectedEntity === v.entity ? 1 : 0}`,
    )
    .join('|');
  if (!force && signature === state.lastListSignature) {
    state.lastListRender = now;
    return;
  }
  state.lastListSignature = signature;
  state.lastListRender = now;

  playerCount.textContent = String(rows.length);
  playersEl.replaceChildren();

  for (const view of rows) {
    const info = classInfo(view.cls);
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'player-row';
    row.dataset.entity = String(view.entity);
    row.classList.add(`team-${teamClass(view.team)}`);
    if (state.selectedEntity === view.entity) row.classList.add('selected');
    if (view.seen && !view.alive) row.classList.add('dead');
    if (view.seen && !view.inPvs) row.classList.add('out-pvs');
    row.title = `${view.name} — ${info.name}`;

    const dot = document.createElement('span');
    dot.className = 'p-dot';

    const main = document.createElement('span');
    main.className = 'p-main';
    const name = document.createElement('span');
    name.className = 'p-name';
    name.textContent = view.name || `#${view.entity}`;
    const meta = document.createElement('span');
    meta.className = 'p-meta';
    if (!view.seen) {
      meta.textContent = t('pNotSeen');
    } else {
      const parts = [info.name];
      if (!view.alive) parts.push(t('pDead'));
      if (!view.inPvs) parts.push(t('pOutOfPvs'));
      meta.textContent = parts.join(' · ');
    }
    main.append(name, meta);

    const hp = document.createElement('span');
    hp.className = 'p-hp';
    hp.textContent = view.seen && view.alive ? String(view.health) : '—';

    row.append(dot, main, hp);
    playersEl.append(row);
  }

  if (rows.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = t('playersEmpty');
    playersEl.append(empty);
  }
}

function entityName(entity: number | null | undefined): string {
  if (entity === null || entity === undefined || entity === 0) return 'world';
  return state.metaUsers.get(entity) ?? `#${entity}`;
}

export function renderKillfeed(force = false): void {
  const loaded = state.loaded;
  if (!loaded) return;
  const tickRate = loaded.tickRate;
  const visible = state.kills
    .filter((k) => k.tick <= state.playbackTick && state.playbackTick - k.tick <= 8 * tickRate)
    .sort((a, b) => b.tick - a.tick)
    .slice(0, 8);

  const signature = visible.map((k) => `${k.tick}:${k.victim}:${k.attacker}`).join('|');
  if (!force && signature === state.lastKillSignature) {
    // just refresh fade opacity
    for (let i = 0; i < state.killItems.length; i++) {
      const kill = visible[i];
      if (!kill) continue;
      const age = (state.playbackTick - kill.tick) / tickRate;
      const opacity = age < 5 ? 1 : Math.max(0, (8 - age) / 3);
      state.killItems[i].style.opacity = String(opacity);
    }
    return;
  }
  state.lastKillSignature = signature;
  killfeedEl.replaceChildren();
  state.killItems.length = 0;

  if (visible.length === 0) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = t('killfeedEmpty');
    killfeedEl.append(empty);
    return;
  }

  for (const kill of visible) {
    const age = (state.playbackTick - kill.tick) / tickRate;
    const opacity = age < 5 ? 1 : Math.max(0, (8 - age) / 3);

    const li = document.createElement('li');
    li.style.opacity = String(opacity);

    const time = document.createElement('span');
    time.className = 'kill-time';
    time.textContent = fmtTime((kill.tick - loaded.minTick) / tickRate);

    const attacker = document.createElement('span');
    attacker.className = 'kill-attacker';
    attacker.textContent = entityName(kill.attacker);

    const victim = document.createElement('span');
    victim.className = 'kill-victim';
    victim.textContent = entityName(kill.victim);

    const weapon = document.createElement('span');
    weapon.className = 'kill-weapon';
    weapon.textContent = kill.weapon || '?';

    li.append(time, attacker, document.createTextNode('✖'), victim, weapon);
    killfeedEl.append(li);
    state.killItems.push(li);
  }
}

// ---------------------------------------------------------------------------
// Language switcher (RU | EN)
// ---------------------------------------------------------------------------

export function refreshLocalizedUi(): void {
  // Static DOM strings are updated by applyLang() inside setLang(); the
  // dynamic ones are re-rendered here (dynamic strings also refresh on the
  // next tick naturally).
  updateAliveStats();
  updateHud();
  renderPlayers(true);
  renderKillfeed(true);
}

for (const btn of document.querySelectorAll<HTMLButtonElement>('#langToggle [data-lang]')) {
  btn.addEventListener('click', () => {
    const lang = btn.dataset.lang === 'ru' ? 'ru' : 'en';
    if (lang === getLang()) return;
    setLang(lang);
    refreshLocalizedUi();
  });
}

// Input handlers extracted from main.ts (behavior-preserving port; main.ts
// remains the source of truth until the wiring is switched over).
//
// This module intentionally does NOT import ui.ts (doesn't exist yet, and it
// would create an import cycle). UI-facing actions (play toggle, mode changes,
// HUD refresh) are injected via the InputHooks interface below.

import { renderer } from './scene';
import { viewport, sidebar } from './dom';
import { clamp } from './tf2';
import { dollyFreeCamera } from './camera';
import { state, type CameraMode } from './state';

export interface InputHooks {
  togglePlay(): void;
  setMode(mode: CameraMode): void;
  updateHud(): void;
  updateModeButtons(): void;
}

// Exported for inspect.ts: tap validity checks `activePointers.size <= 1` on
// touch pointerdown (second finger = pinch, not a tap).
export const activePointers = new Map<number, { x: number; y: number }>();

export function setupInput(hooks: InputHooks): void {
  // Mouse look (right mouse button) / touch look (single-finger drag) / pinch
  renderer.domElement.addEventListener('contextmenu', (event) => event.preventDefault());

  let pinchPrevDist = 0;

  renderer.domElement.addEventListener('pointerdown', (event) => {
    const isTouch = event.pointerType === 'touch';
    if (!isTouch && event.button !== 2) return;
    if (isTouch) {
      activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (activePointers.size >= 2) {
        // second finger → pinch gesture, cancel look drag
        state.lookDragging = false;
        const pts = [...activePointers.values()];
        pinchPrevDist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
        return;
      }
    }
    state.lookDragging = true;
    state.lastPointerX = event.clientX;
    state.lastPointerY = event.clientY;
    renderer.domElement.setPointerCapture(event.pointerId);
  });

  window.addEventListener('pointermove', (event) => {
    if (activePointers.has(event.pointerId)) {
      activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (activePointers.size >= 2) {
        const pts = [...activePointers.values()];
        const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
        if (pinchPrevDist > 0 && dist > 0) {
          // Two-finger pinch: zoom. Free mode → dolly; follow/pov → follow distance.
          const factor = pinchPrevDist / dist; // spread out = move forward
          if (state.cameraMode === 'free') {
            dollyFreeCamera(factor);
            hooks.updateHud();
          } else {
            state.followDistance = clamp(state.followDistance * factor, 80, 4000);
            state.zoomFlashUntil = performance.now() + 900;
            hooks.updateHud();
          }
        }
        pinchPrevDist = dist;
        return;
      }
    }
    if (!state.lookDragging) return;
    const dx = event.clientX - state.lastPointerX;
    const dy = event.clientY - state.lastPointerY;
    state.lastPointerX = event.clientX;
    state.lastPointerY = event.clientY;
    if (state.cameraMode !== 'free') return;
    state.freeYaw -= dx * 0.0026;
    state.freePitch = clamp(state.freePitch - dy * 0.0026, -1.5, 1.5);
  });

  // Pinch zoom: two-finger spread/pinch dollies the free camera along its view
  // direction ("normally zoom in with gestures"). Multiplier from the ratio is
  // smoothed so it doesn't jump.

  renderer.domElement.addEventListener('wheel', (event) => {
    if (state.cameraMode === 'free') {
      const factor = Math.exp(event.deltaY * 0.0012);
      dollyFreeCamera(factor);
      hooks.updateHud();
    } else if (state.cameraMode === 'follow') {
      state.followDistance = clamp(state.followDistance * Math.exp(event.deltaY * 0.0012), 80, 4000);
      state.zoomFlashUntil = performance.now() + 900;
      hooks.updateHud();
    }
  }, { passive: true });

  function endPointer(event: PointerEvent): void {
    activePointers.delete(event.pointerId);
    if (activePointers.size < 2) pinchPrevDist = 0;
    state.lookDragging = false;
  }

  window.addEventListener('pointerup', endPointer);
  window.addEventListener('pointercancel', endPointer);

  window.addEventListener('keydown', (event) => {
    const target = event.target as HTMLElement | null;
    const typing =
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement;

    if (event.code === 'Space' && !typing) {
      event.preventDefault();
      hooks.togglePlay();
      return;
    }
    if (event.code === 'Escape') {
      state.cameraMode = 'free';
      hooks.updateModeButtons();
      hooks.updateHud();
      return;
    }
    if (event.code === 'KeyF' && !typing) {
      hooks.setMode('follow');
      return;
    }
    if (event.code === 'KeyV' && !typing) {
      hooks.setMode('pov');
      return;
    }

    state.keys.add(event.code);
  });

  window.addEventListener('keyup', (event) => {
    state.keys.delete(event.code);
  });

  window.addEventListener('blur', () => state.keys.clear());

  // Touch controls (joystick, elevation slider, bottom sheet)
  setupTouchControls();
  setupBottomSheet();
}

// --- virtual joystick: x = strafe (A/D), y = forward/back (W/S) ---
function setupTouchControls(): void {
  if (!state.isCoarse) return;

  const stick = document.createElement('div');
  stick.id = 'joyStick';
  const base = document.createElement('div');
  base.className = 'joy-base';
  const knob = document.createElement('div');
  knob.className = 'joy-knob';
  base.append(knob);
  stick.append(base);

  const slider = document.createElement('div');
  slider.id = 'elevSlider';
  const grip = document.createElement('div');
  grip.className = 'elev-grip';
  slider.append(grip);

  viewport.append(stick, slider);

  const JOY_RADIUS = 42;
  const JOY_DEADZONE = 0.16;
  let joyId: number | null = null;
  let joyCenterX = 0;
  let joyCenterY = 0;

  const moveJoy = (event: PointerEvent): void => {
    if (event.pointerId !== joyId) return;
    let dx = event.clientX - joyCenterX;
    let dy = event.clientY - joyCenterY;
    const dist = Math.hypot(dx, dy);
    if (dist > JOY_RADIUS) {
      dx = (dx / dist) * JOY_RADIUS;
      dy = (dy / dist) * JOY_RADIUS;
    }
    knob.style.transform = `translate(${dx}px, ${dy}px)`;
    const nx = dx / JOY_RADIUS;
    const ny = dy / JOY_RADIUS;
    state.joySide = Math.abs(nx) < JOY_DEADZONE ? 0 : nx;
    state.joyForward = Math.abs(ny) < JOY_DEADZONE ? 0 : -ny; // up = forward
  };

  base.addEventListener('pointerdown', (event) => {
    if (joyId !== null) return;
    joyId = event.pointerId;
    const rect = base.getBoundingClientRect();
    joyCenterX = rect.left + rect.width / 2;
    joyCenterY = rect.top + rect.height / 2;
    base.setPointerCapture(event.pointerId);
    event.preventDefault();
    moveJoy(event);
  });
  base.addEventListener('pointermove', moveJoy);
  const endJoy = (event: PointerEvent): void => {
    if (event.pointerId !== joyId) return;
    joyId = null;
    state.joyForward = 0;
    state.joySide = 0;
    knob.style.transform = 'translate(0px, 0px)';
  };
  base.addEventListener('pointerup', endJoy);
  base.addEventListener('pointercancel', endJoy);

  // --- elevation slider: drag up/down = Q/E (fly down/up) ---
  let elevId: number | null = null;
  const setElev = (event: PointerEvent): void => {
    const rect = slider.getBoundingClientRect();
    const mid = rect.top + rect.height / 2;
    const half = Math.max(1, rect.height / 2 - 16);
    const value = clamp((mid - event.clientY) / half, -1, 1);
    state.elevAxis = Math.abs(value) < 0.14 ? 0 : value;
    grip.style.transform = `translateY(${(-value * half).toFixed(1)}px)`;
  };
  slider.addEventListener('pointerdown', (event) => {
    if (elevId !== null) return;
    elevId = event.pointerId;
    slider.setPointerCapture(event.pointerId);
    event.preventDefault();
    setElev(event);
  });
  slider.addEventListener('pointermove', (event) => {
    if (event.pointerId === elevId) setElev(event);
  });
  const endElev = (event: PointerEvent): void => {
    if (event.pointerId !== elevId) return;
    elevId = null;
    state.elevAxis = 0;
    grip.style.transform = 'translateY(0px)';
  };
  slider.addEventListener('pointerup', endElev);
  slider.addEventListener('pointercancel', endElev);
}

function setupBottomSheet(): void {
  const sheetHandle = document.getElementById('sheetHandle');
  if (!sheetHandle) return;

  const setSheet = (expanded: boolean): void => {
    sidebar.classList.toggle('expanded', expanded);
    sheetHandle.setAttribute('aria-expanded', String(expanded));
  };

  sheetHandle.addEventListener('click', () => {
    setSheet(!sidebar.classList.contains('expanded'));
  });

  // swipe up on the handle expands, swipe down collapses
  let startY: number | null = null;
  sheetHandle.addEventListener(
    'touchstart',
    (event) => {
      startY = event.touches[0]?.clientY ?? null;
    },
    { passive: true },
  );
  sheetHandle.addEventListener(
    'touchmove',
    (event) => {
      if (startY === null) return;
      const dy = (event.touches[0]?.clientY ?? startY) - startY;
      if (dy < -28) {
        setSheet(true);
        startY = null;
      } else if (dy > 28) {
        setSheet(false);
        startY = null;
      }
    },
    { passive: true },
  );
  sheetHandle.addEventListener('touchend', () => {
    startY = null;
  });
}

# Viewer internals (web/src)

- Date: 2026-10-21
- Author: lead agent
- Scope: `web/src/main.ts`, `web/src/replay.ts`, `web/src/playerModels.ts`, `web/src/i18n.ts`
- Tags: #viewer #camera #playback

## State

Single-page three.js app. `main.ts` is a thin 20-line bootstrap; all logic
lives in `web/src/viewer/` (camera.ts, playback.ts, playerViews.ts,
inspect.ts, input.ts, damageFx.ts, projectiles.ts, nameLabels.ts,
frameCache.ts, dom.ts). Loads `/maps/<map>.glb` and `/replay/replay.bin`,
plays back tick-by-tick with interpolation.

## Gotchas

- **Coordinate mapping is load-bearing.** Source Z-up → scene Y-up via
  `gltf.scene.rotation.x = -PI/2`, and every position maps `(x,y,z) -> (x,z,-y)`.
  This mapping appears in at least 6 places (players, projectiles, damage
  numbers, splash rings, prop metadata, POV camera). Changing one without the
  rest breaks them silently.
- **`raycaster.far` is shared state.** The follow-camera wall-collision sets
  `raycaster.far = len` for its short ray. Any other raycast (click-inspect)
  MUST reset `far` or it will see nothing after following a player. This caused
  the "inspect doesn't work" bug.
- **Follow pose lookup**: `lastPoses` is only populated during playback; when
  selecting a player while paused, use `posesAt(playbackTick)` directly
  (implemented in updateCamera). `followSnap` forces an immediate teleport to
  the desired position on mode/player change (prevents cross-map camera flights).
- **Model swap caching**: player models are swapped only when `(cls, team)`
  changes (see `view.modelKey`); templates are cached in `playerModels.ts`
  (module-level Maps). Materials are cloned per player instance so opacity/
  emissive tinting (dead 0.24, out-of-PVS 0.35, cloaked spy 0.45, disguised spy
  purple emissive) never leaks between players.
- **Player models are plain (non-DRACO) GLBs** exported from the local VPK
  by tools/mdl2glb.py into web/public/models/players/ (18 files, real TF2
  textures embedded as PNG). DRACOLoader was removed from playerModels.ts
  and modeltest.ts — no external decoder dependency. The test scene
  `/modeltest.html` exists to debug model loading in isolation.
- **replay.bin parsing** is centralized in `replay.ts`; `PROJ_RECORD_SIZE = 17`
  and `HURT_RECORD_SIZE = 15` must match the Rust writer (see 10-replay-format.md).
- **Damage FX lifecycle**: popups and splash rings are spawned when
  `updateDamageFx()` first sees a frame's hurt entries (frame index from
  `findFrameIndex`); they age by `playbackTick - bornTick`. Scrubbing backwards
  does not re-spawn (by design).
- **Mobile**: bottom sheet (max-width 820px) collapses to a bar; joystick and
  elevation slider are created only for `pointer: coarse`; pinch = camera speed
  in free mode, follow distance in follow mode. Desktop right-drag look is
  unchanged; one-finger touch drag = look.
- **i18n**: all user-facing strings via `t()` from `i18n.ts`; dynamic strings
  re-render on language switch through `updateHud`/`updateAliveStats`/forced
  re-renders. localStorage key `tf2viewer.lang`.

## Open questions

- Verify click-inspect end-to-end in a real browser (was fixed blind:
  `raycaster.far` reset + ray-nearest fallback).
- Model facing direction: assumed +Z forward; if players walk backwards in
  scene, add a Y-offset on `modelHolder` (single line).

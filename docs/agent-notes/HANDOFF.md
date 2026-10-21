# Handoff Summary — session of 2026-10-21

Read this first, then `../AGENTS.md`, then `STATUS.md`. Total project commits:
33 (all pushed to https://github.com/ea29ia/tf2-demo-viewer, master, clean tree).

## What was done this session (chronological, all verified)

1. **Project bootstrap**: Rust dumper (`preprocess/`) producing `replay.bin` v2
   + `meta.json` from a TF2 POV demo; validated end-to-end on
   `demo_upward1.dem` (pl_upward, 55842 frames, 24 players, 228 kills).
2. **Map pipeline**: `tools/` (stdlib-only python) converts BSP v20 → GLB:
   brushes + displacements (`bsp2glb.py`), 1242 oriented prop boxes with full
   QAngle rotation + foliage split (`add_props_glb.py`), 30 kit pads from the
   entity lump (`add_kits_glb.py`), validator (`validate_glb.py` → PASS).
   Map downloaded from fastdl via `tools/get_map.sh`.
3. **Viewer** (`web/`, Vite+TS+three.js): map render, players, projectiles,
   damage numbers, splash rings, kit pads, three cameras, playback, killfeed,
   counters, mobile UI, RU/EN i18n. Builds clean (`tsc --noEmit` + `vite build`).
4. **Features added on user request** (in order): team-colored capsules →
   real player models (18 DRACO glbs from dribble.tf, civilian pose, capsule
   fallback, "Show models" toggle), spy visuals (purple + true-team ring,
   cloak translucency), view-direction arrows, follow-camera wall collision +
   adjustable follow distance, pinch/wheel zoom, damage numbers + splash rings,
   kit pads, foliage hide toggle (default ON), flat shading + shadows, mobile
   bottom sheet/joystick, RU/EN switch, click-inspect info card,
   `/modeltest.html` test scene, alive/dead counters + time remaining.
5. **Key bugs fixed** (all root-caused, see decisions.md):
   - replay writer/reader record-size mismatch (projectiles) — "missing files".
   - `raycaster.far` shared-state bug killing click-inspect after follow.
   - Missing DRACOLoader — models silently fell back to capsules.
   - validate.py did not know the hurt block / new flag bits (found during
     this handoff audit; fixed).
6. **Research**: POV-demo PVS limits measured from real data (max in-PVS
   distance 5092u, median out 889u); hit-scan data confirmed present
   (CTEFireBullets, 4217 shots/match); sniper-aim-stats design written up.
7. **Docs layer**: `docs/agent-notes/` (STATUS, 3 topic notes, decisions log,
   strict note format), AGENTS.md rules, PLAN.md now documents v2 layout.

## Known limitations (accepted, not bugs)

- POV demo PVS blindness (inherent; free camera beyond recorder path = empty).
- Pyro model is the MvM bot mesh; models have no bones (pose baked); no
  animations; props are boxes — all blocked on TF2 VPK access (fastdl doesn't
  serve VPKs; SteamCMD unavailable on this aarch64-android box).
- Click-inspect fix (raycaster.far reset) is verified by code review, not yet
  by a real browser click — first thing to check live.

## Recommended next tasks (in order)

1. **Verify click-inspect in a real browser** (open /5173, click a rail —
   info card should appear; if not, debug with the `[inspect] prop entries
   loaded: N` console log; N must be 1272).
2. **Hit-scan shot lines** (user asked "can we draw them" — yes; needs replay
   v3: `shots` block in writer+reader+PLAN.md, one commit, bump VERSION).
   Design + data locations are in TODO.txt "Sniper aim statistics" section —
   the same v3 unlocks both the lines and the stats.
3. **PVS coverage heatmap** (user asked; design agreed in chat: aggregate
   positions across N replays of the same map by steamId; render as an overlay
   layer in the viewer). Start single-demo, then multi-demo aggregation.
4. **QoL top-5** (user asked for a list, agreed on these): jump-to-death from
   killfeed, timeline bookmarks, state persistence in localStorage, event
   markers on the timeline, weapon icons in killfeed.
5. Bigger items (VPK-dependent or new systems) are in TODO.txt — do not start
   without the user's go-ahead.

## Files to open first (in this order)

1. `../AGENTS.md` — hard rules (English-only comments, format contract,
   verify-before-done, env quirks).
2. `STATUS.md` — current state + validation command block.
3. `../PLAN.md` — replay.bin v2 binary layout (the contract).
4. `10-replay-format.md` — record sizes/gotchas if touching the format.
5. `30-viewer-internals.md` — if touching `web/src/main.ts` (coordinate
   mapping, raycaster.far, model swap caching, i18n wiring).
6. `decisions.md` — why things are the way they are (append, never delete).
7. `../TODO.txt` — roadmap; do not start big items without user approval.

## Environment quick facts

- aarch64 Android (Termux/proot). Rust builds need
  `preprocess/.cargo/config.toml` (clang + `-L/system/lib64`) — present, keep.
- `gh` CLI is dead here (Go TLS timeout); use curl for GitHub API, ssh for push.
- Dev server: `cd web && npm run dev` → http://localhost:5173 (vite hot-reloads;
  a stale instance may already hold ports 5173/5199).
- User communicates in Russian; all code/docs/comments in English.

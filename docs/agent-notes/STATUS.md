# Project Status — tf2-demo-viewer

- Date: 2026-10-21 (refreshed after full audit)
- Author: lead agent
- Scope: whole project
- Tags: #status

Read `README.md` in this directory first (rules for notes). Format details live
in `PLAN.md`, project rules in `AGENTS.md`, roadmap in `TODO.txt`.

## What this project is

Browser viewer for TF2 POV demos. Pipeline: `.dem` → Rust dumper
(`preprocess/src/main.rs`) → `replay.bin` v2 + `meta.json`; `map.bsp` → python
tools (`tools/`) → `map.glb`; both are served by Vite and rendered by a
three.js app (`web/`). Proven on one demo (`demo_upward1.dem`, pl_upward,
837 s, 24 players, 228 kills, 4217 hit-scan shots). Repo:
https://github.com/ea29ia/tf2-demo-viewer (public).

## Working (verified 2026-10-21 with the exact commands below)

- **Replay data** (`web/public/replay/`): 55842 contiguous frames (ticks
  0..55841), max 24 players/frame, up to 12 projectiles, up to 11 hurt events
  per frame. Player record 26 B (flags bit0 alive, bit1 in_pvs, bit2 disguised,
  bit3 stealthed), projectile 17 B (quantized i16), hurt 15 B (incl. splash
  radius by weapon id). Validate:
  `python3 preprocess/validate.py web/public/replay/replay.bin web/public/replay/meta.json` → OK.
- **Map GLB** (`web/public/maps/pl_upward.glb`, 4.6 MB): brushes + 558
  displacements + 1242 oriented prop boxes + 67 foliage props in a separate
  `foliage` node + 30 kit pads; click-inspect metadata in `nodes[0].extras.props`
  (Source-space). Validate:
  `python3 tools/validate_glb.py web/public/maps/pl_upward.glb` → PASS.
- **Web build**: `cd web && npx tsc --noEmit && npm run build` → both exit 0
  (dist ~670 kB JS). Dev server: `npm run dev` (port 5173).
- **Viewer features** (all user-visible):
  - Real player models (18 glb, DRACO-compressed, from dribble.tf; civilian
    pose, no animations), capsule fallback, "Show models" toggle (default ON).
  - Team colors red `#e03c3c` / blue `#2f7bff`; spies purple + true-team ring
    when disguised, translucent when cloaked; view-direction arrows; name/HP
    labels; dead fade 0.24, out-of-PVS fade 0.35.
  - Projectiles (rockets/pipes/stickies/flares/arrows) oriented by velocity;
    explosive projectiles spawn a splash ring on disappearance (146/148 u).
  - Damage numbers from player_hurt (white, crit yellow) with float+fade.
  - Kit floor pads (green health / amber ammo) from BSP entity lump
    (`kits.json`).
  - Cameras: free-fly (WASD+QE, right-drag look, wheel dolly), follow
    (wall-collision raycast, distance 80..4000 via wheel/pinch, defaults to
    recorder by meta.nick), POV (eye height, recorded yaw/pitch).
  - Playback: timeline scrub, 0.25–4x, interpolation with 512 u teleport snap.
  - Killfeed, alive/dead counters, time remaining, mobile bottom sheet +
    joystick + pinch speed, RU/EN UI (`i18n.ts`), click-inspect info card
    (`raycaster.far` reset + ray-nearest fallback), model test scene at
    `/modeltest.html`.

## Known issues / limitations (not bugs in code)

- POV demo: entities outside the recorder's PVS are absent from the data.
  Measured on this demo: max in-PVS distance 5092 u, median out-transition 889 u.
  A free camera far from the recorder sees empty space — inherent to POV demos.
- Pyro model is the MvM bot mesh (materials `pyro_bot_*`) — the dribble.tf set
  has no vanilla pyro.
- Player models have no bones; the weapon-holding pose is baked in. No
  animations yet (sequence/cycle not exported to replay.bin).
- Props are boxes; real meshes blocked on TF2 VPK access (fastdl does not serve
  VPKs; SteamCMD not available on this aarch64-android box).
- Hit-scan shot lines and sniper aim statistics are designed but not
  implemented (see TODO.txt; needs replay v3).

## Not implemented / parked

See `TODO.txt` (English). Big items: cart animation along path_track, real
prop/player meshes (VPK), map textures, WASM in-browser parsing, PVS heatmap +
multi-demo aggregation (user-approved design, not started), sniper aim stats.

## Validation commands (run after touching the matching area)

```sh
# dumper or format changed:
cd preprocess && cargo build --release
./target/release/dem2replay <demo.dem> ../web/public/replay
python3 validate.py ../web/public/replay/replay.bin ../web/public/replay/meta.json   # → OK

# tools or map changed:
python3 tools/bsp2glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb
python3 tools/add_props_glb.py web/public/maps/pl_upward.glb assets/pl_upward.bsp
python3 tools/add_kits_glb.py web/public/maps/pl_upward.glb
python3 tools/validate_glb.py web/public/maps/pl_upward.glb                          # → PASS

# web changed:
cd web && npx tsc --noEmit && npm run build
```

## Environment quirks (do not rediscover)

- aarch64-android (Termux/proot): Rust needs `preprocess/.cargo/config.toml`
  (clang linker + `-L/system/lib64`) — present, do not remove.
- `gh` CLI cannot reach api.github.com (Go TLS handshake timeout, likely DPI);
  curl works, git push over ssh works. Create/edit repos via curl + API.
- Valve assets are gitignored (`.bsp`, `.glb`, `replay.bin`, player model
  glbs). Only code and small data (kits.json) are committed.

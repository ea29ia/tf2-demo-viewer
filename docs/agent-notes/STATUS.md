# Project Status — tf2-demo-viewer

- Date: 2026-10-21
- Author: lead agent (this session)
- Scope: whole project
- Tags: #status

## What this project is

Browser viewer for TF2 POV demos. Pipeline: `.dem` → Rust dumper (`preprocess/`)
→ `replay.bin` v2 + `meta.json`; `map.bsp` → python tools → `map.glb`; both are
served statically and rendered by a three.js app (`web/`). Currently proven on
one demo (`demo_upward1.dem`, pl_upward, 837s, 24 players, 228 kills).

## Current state (all verified with commands listed in AGENTS.md)

### Working
- **Dumper** (`preprocess/`): parses the demo via tf-demo-parser 0.7.1, emits
  55842 frames (one per tick, gaps filled by repeating the last snapshot),
  max 24 players, plus projectiles (12 max) and hurt events. Records: player 26B,
  projectile 17B (quantized i16 /4), hurt 15B. Flags: bit0 alive, bit1 in_pvs,
  bit2 disguised, bit3 stealthed. Validated by `preprocess/validate.py` → OK.
- **Map GLB** (`web/public/maps/pl_upward.glb`, 4.6 MB): brush geometry +
  558 displacements + 1242 static props as oriented boxes (full pitch/yaw/roll
  rotation) + 67 foliage props in a separate `foliage` mesh node + 30 kit pads
  (health/ammo floor markers from BSP entity lump, `web/public/maps/kits.json`).
  Props/kit metadata in `nodes[0].extras.props` (1272 entries: name, Source-space
  center, size) for click-inspect. `validate_glb.py` → PASS.
- **Viewer** (`web/`): three.js. Map with flat shading + real shadows (2048 map).
  Players: real glTF models (civilian pose, DRACO-compressed, from dribble.tf) —
  fallback to capsules if load fails or toggle off ("Show models" checkbox, default
  ON). Note: pyro model is the MvM bot mesh (materials `pyro_bot_*`), source repo
  has no vanilla one. Spies: purple emissive + true-team ring when disguised,
  translucent when cloaked. Projectiles rendered and oriented by velocity;
  explosive projectiles spawn a splash ring on disappearance. Damage numbers
  (white/crit yellow) from player_hurt events with floating+fade animation.
  Cameras: free-fly, follow (wall-collision raycast keeps camera out of geometry,
  distance adjustable 80..4000 by wheel/pinch, defaults to the demo recorder),
  POV. Playback: timeline, 0.25-4x speed, interpolation with 512u teleport snap.
  Killfeed, alive/dead counters, time remaining, mobile bottom sheet + joystick
  + pinch. UI in RU/EN (`i18n.ts`, toggle in sidebar header, localStorage).
  Click-inspect: raycast + nearest-prop-within-tolerance with ray fallback;
  info card with localized labels. Model test scene: `/modeltest.html`.

### Known broken / missing
- Click-inspect reported "not clicking" once; fixed (raycaster.far reset +
  ray-nearest fallback) but needs re-verification from a real browser session.
- Foliage hidden by default; hidden foliage is excluded from camera collision —
  intentional.
- No hit-scan shot rays yet (data exists in demo TempEntities, see TODO).
- Sniper aim statistics — designed, parked in TODO.txt.

### Environment quirks (do not rediscover these)
- Rust target is aarch64-android; `preprocess/.cargo/config.toml` (clang linker
  + `-L/system/lib64`) is required — do not remove.
- `gh` CLI cannot reach api.github.com from this box (Go TLS handshake timeout,
  likely DPI); curl and git-over-ssh work. Use curl for GitHub API, ssh for push.
- Repo: https://github.com/ea29ia/tf2-demo-viewer (public). Valve assets are
  gitignored; models live only locally in `web/public/models/players/`.

## In flight
- Nothing at the moment (last session ended after player models landed).

## Next likely tasks (see TODO.txt for the full list)
- Hit-scan shot lines (data exists; replay v3 needed).
- PVS coverage heatmap; multi-demo aggregation (user asked, design agreed in chat).
- Real prop meshes / vanilla pyro — blocked on TF2 VPK access.

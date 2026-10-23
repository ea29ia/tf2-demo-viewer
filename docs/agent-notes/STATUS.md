# Project Status — tf2-demo-viewer

- Date: 2026-10-21 (refreshed after full audit)
- Author: lead agent
- Scope: whole project
- Tags: #status

Read `README.md` in this directory first (rules for notes). Format details live
in `PLAN.md`, project rules in `AGENTS.md`, roadmap in `TODO.txt`.

## What this project is

Browser viewer for TF2 POV demos. Pipeline: `.dem` → Rust dumper
(`preprocess/src/main.rs`) → `replay.bin` v3 + `meta.json`; `map.bsp` → python
tools (`tools/`) → `map.glb`; both are served by Vite and rendered by a
three.js app (`web/`). Proven on one demo (`demo_upward1.dem`, pl_upward,
837 s, 24 players, 228 kills, 4217 hit-scan shots). Repo:
https://github.com/ea29ia/tf2-demo-viewer (public).

## Working (verified 2026-10-21 with the exact commands below)

- **Health fix + replay.bin v3 (2026-10-22)**: player record `health` is now
  `u16` (record 27 bytes, VERSION 3; writer, reader and validate.py agree).
  Health sourcing fixed in the dumper: `player_hurt` event health overrides
  stale out-of-PVS sendprops, dead players report 0 (sendprop reads 1), and a
  respawn (dead->alive within 30 s of a known death) resets health to the
  class base because PVS re-entry resurfaces a stale per-entity instance
  baseline (same wrong constant after every respawn). Game events are
  retransmitted in later packets; the hurt cursor now dedupes by content hash
  and processes events at first sight. Audited with the new
  `tools/health_audit.py` (class ids follow the parser's Class enum = TF2 ids;
  respawn windows gated on kill events): dustbowl 223 OK / 2 mismatch,
  upward 123 OK / 4 mismatch (residuals are stale resource class labels, not
  wrong health). Verified: cargo build + dumps + validate.py OK on both demos,
  `tsc --noEmit` + `npm run build` exit 0.

- **Dumper signon/hurt fixes (2026-10-21)**: frame emission now starts at the
  first tick-0 packet (signon phase suppressed), fixing a backwards timeline on
  demos whose signon tick is below `header.ticks` (cp_dustbowl); hurt events are
  consumed via a forward cursor in `main.rs` (the analyser never clears
  `state.events`). On-disk format unchanged, still v2. Verified on both
  dustbowl and upward demos (validate.py OK).

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
python3 tools/add_props_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb
python3 tools/add_kits_glb.py web/public/maps/pl_upward.glb web/public/maps/kits.json
python3 tools/validate_glb.py web/public/maps/pl_upward.glb                          # → PASS

# web changed:
cd web && npx tsc --noEmit && npm run build
```

## Environment quirks (do not rediscover)

- aarch64-android (Termux/proot): Rust needs `preprocess/.cargo/config.toml`
  (clang linker + `-L/system/lib64`) — present, do not remove it (inert on
  x86_64 because it is target-scoped). The x86_64 desktop has Node in
  `~/.local/node/bin` and Rust in `~/.cargo/bin`, and a local TF2 install.
- `gh` CLI cannot reach api.github.com (Go TLS handshake timeout, likely DPI);
  curl works, git push over ssh works. Create/edit repos via curl + API.
- Valve assets are gitignored (`.bsp`, `.glb`, `replay.bin`, player model
  glbs). Only code and small data (kits.json) are committed.

## Session update — 2026-10-22 (later)

- Killfeed moved out of the sidebar into a floating overlay at the viewport's
  top-right corner (`.killfeed-overlay`, game-style; sidebar card hidden,
  `#killfeed` element unchanged so `renderKillfeed` needed no changes).
- New map toggles in the sidebar: mesh wireframe (material.wireframe on all
  map+foliage meshes) and prop bounding-box wireframe overlay (one merged
  `LineSegments` built from GLB extras in `inspect.buildPropBoxOverlay`,
  ~1272 boxes, single draw call, AABB-only — GLB extras don't store prop yaw).
- `tools/bsp2glb.py`: out-of-world face culling now intersects worldspawn
  `world_mins/world_maxs` with the old sane constants (some maps seal their
  3D skybox inside worldspawn bounds — upward; others place it outside —
  dustbowl). Both maps validate PASS.
- Second demo supported end-to-end: `demo_dustbowl1.dem` (cp_dustbowl,
  110842 frames, 24 players, 391 kills) dumped + validated; GLB built from
  fastdl bsp; `MAP_URL` currently points at cp_dustbowl.glb (upward replay
  kept in web/public/replay/backup_upward/, not in git).
- Click-to-inspect still broken (user reports clicks don't fire); deferred.

## Session update — 2026-10-22 (late night): "geometry explosion" fixed

- User-reported spikes/shards sticking out of the map. Root cause (found via
  two parallel subagents: clean rebuild + triangle-level forensics):
  `add_flat()` in `tools/bsp2glb.py` fan-triangulated brush faces polluted
  with near-duplicate/collinear vertices → ~21k degenerate slivers rendered
  as needles. NOT props, NOT kits, NOT the new player models.
- Fix: `clean_polygon()` + `ear_clip()` in bsp2glb.py; upward base mesh
  65136 → 43994 tris; with props+kits 57914 tris, validate PASS.
  `validate_glb.py` threshold recalibrated (min 35000 tris).
- Verified in headless chromium: default view clean, close-ups at the former
  spike apexes (1408,926,297) and (167,884,-7) show normal geometry,
  tsc + build exit 0. Commit 813847d. Debug hooks (window.__scene/__cam)
  removed after use — re-add temporarily if scene forensics is needed again.

## Session update — 2026-10-22 (night): VPK player models shipped
- **All 9 classes × 2 teams exported from the local VPKs** into
  `web/public/models/players/` (18 GLBs, ~9 MB total, gitignored — rebuild
  with `tools/mdl2glb.py <tf_dir> models/player/<class> web/public/models/players/<class>`,
  note: demoman's model path is `models/player/demo`, heavy is `models/player/heavy`
  with materials in `models/player/hvyweapon/`). The dribble.tf DRACO set and
  the MvM robo-pyro are gone; DRACOLoader removed from playerModels.ts and
  modelTest.ts (no more gstatic CDN dependency).
- **mdl2glb.py fixes** (all hit during the batch export):
  - Empty placeholder models in bodyparts (nummodels=2, second model is
    padding with garbage fields) caused out-of-bounds reads on
    soldier/medic/engineer/sniper/spy — added bounds guards + skip when
    nummeshes/numvertices <= 0.
  - Bare material names must NOT be prefixed with the model's directory:
    heavy's model is models/player/heavy but materials are
    models/player/hvyweapon. New `Textures.resolve_material()` does a
    VPK-wide search over materials/models/player/* when direct paths miss.
  - Axis conversion added: VVD model space is RH with up=+y, forward=+z,
    left=+x (NOT z-up! verified via eyeball pair: eyeball_l at +x, eyes at
    +z front). Mapping to glTF: (x,y,z) -> (z,y,-x) so the model faces its
    local +x, which is what the viewer needs (group.rotation.y = yaw,
    forward=(cos yaw, 0, -sin yaw)). Feet land at y=0; heights 79-87 u.
- **Verification**: headless chromium (playwright-core, browser in
  ~/.cache/ms-playwright) rendered `_modelcheck.html` (temp page, since
  removed) and the real app: all GLB requests 200, no page errors, no
  model-fallback warnings; tsc + vite build exit 0. Committed 22a2620.
  Not yet verified: user eyeball check of facing direction in motion.
- Known cosmetic: eyeball meshes render with the flat iris texture (no
  cornea refraction) — acceptable for now.

## Session update — 2026-10-22 (evening, x86_64 desktop)

- **Environment changed**: this session runs on x86_64 Linux (not the Termux
  aarch64 box). Node 24 and Rust 1.99 were installed locally (`~/.local/node`,
  `~/.cargo`); `preprocess/.cargo/config.toml` only targets
  `aarch64-linux-android`, so it does not affect this host.
- **Valve assets available locally**: TF2 is installed at
  `~/.steam/debian-installation/steamapps/common/Team Fortress 2` with
  `tf/maps/*.bsp`, all `tf2_*.vpk` files and demo recordings
  (`tf/demp.dem`, `demp_2.dem`, `pocket.dem`, `pocket1.dem`, `fix.dem`).
  This unblocks the VPK-dependent TODO items (real player/prop meshes,
  map textures, vanilla pyro) — needs user go-ahead before starting.
- **Assets regenerated** from `tf/demp.dem` (pl_upward, 1434 s, 95349 frames,
  24 players, 487 kills) and from the local `pl_upward.bsp`:
  - `replay.bin` 64 MB, `validate.py` → OK.
  - map GLB 4.38 MiB, 79056 triangles, 1182 prop entries + 30 kits,
    `validate_glb.py` → PASS; bbox now x[-3072, 4100.6] (no more skybox props).
  - `state.ts` `MAP_URL` back to `maps/pl_upward.glb` (was cp_dustbowl).
- **Bugs fixed (verified in a real browser tab)**:
  - Click-inspect loaded 0 props: GLTFLoader merges `node.extras` into
    `object.userData`, so the code must read `userData.props`, not
    `userData.extras.props`. Now logs `[inspect] prop entries loaded: 1212`;
    synthetic clicks returned barrels, rails, fences, boulders, lights.
  - 3D-skybox props were included in the GLB (bbox max x 8898, floating boxes
    far from the map). `add_props_glb.py` now culls against worldspawn
    world_mins/maxs intersected with the sane constants, like bsp2glb.py.
- **Verified commands**: `cargo build --release` OK; dumper OK;
  `validate.py` OK; `tools/validate_glb.py` PASS; `npx tsc --noEmit` exit 0;
  `npm run build` exit 0. Dev server on http://127.0.0.1:5173.

## Session update — 2026-10-22 (late night): ROOT CAUSE of broken textures found

- **vtf.py decoded the wrong mip.** VTF stores the mip chain smallest-first;
  mip 0 (full res) is the LAST mip_size bytes of the image data. We read from
  the start (thumbnails + head of bigger mips) → spatially scrambled texture
  for every material. Decisive test: box-2x2 downscale of decoded mip0 vs
  decoded mip1 — mean|diff| 1.0 after fix vs 41.4 before. After the fix the
  medic face verts sample skin (59-70% skin-rate, avg (167,102,75)); before:
  5%, avg (28,27,28). All 18 player GLBs regenerated (commit 706c28b).
- **mdl2glb.py: winding fix** (f03389e): Source VTX strips are D3D CW;
  glTF needs CCW — emit (a,c,b). Verified: geometric-vs-vertex normal
  agreement 3129/3139 on the medic head after fix (was 10/3139).
- **web: forced TextureLoader path** (71108fd): `window.createImageBitmap =
  undefined` before GLTFLoader in playerModels.ts + modelview.html —
  ImageBitmapLoader ignores texture.flipY at upload; browser orientation
  disagreements mirror islands. tsc + vite build exit 0.
- User to verify visually in Firefox; [dbg] console.log still in
  modelview.html — remove after confirmation.

## 2026-10-22: offline TF2 data copy for phone-based development

- `~/Documents/tf2/` (328 MB) now holds an offline subset of the Steam TF2
  install, used when the desktop is unavailable:
  - `tf/materials/`, `tf/models/` — exactly the files mdl2glb needs: the 9
    base player models (mdl/vvd/dx90/dx80/sw.vtx), hwm variants, all
    materials/models/player VMTs, and all non-item VTFs for player
    materials (~117 MB; the `items/` cosmetics subtree was skipped).
  - `tf/models/player/*_animations.mdl` (9 files, 44 MB) — the composite
    animation libraries; needed for future sequence/skinning work.
  - `demos/` — demp.dem, demp_2.dem, pocket.dem, pocket1.dem, fix.dem.
  - `assets/pl_upward.bsp` already lives in the repo working tree.
- `tools/vpk.py` gained `LoosePack` + `open_pack(tf_dir, name)`: plain
  directory tree works wherever a VPK set did. `mdl2glb.py` uses it for
  both the model pack and `Textures`. Verified: medic GLB from the loose
  tree is byte-identical (JSON) to the VPK-run output (8288 tris).
- Usage from the offline copy:
  `python3 tools/mdl2glb.py ~/Documents/tf2/tf models/player/<cls> web/public/models/players/<cls>`
  (demoman dir is `models/player/demo`).

## Session update — 2026-10-23: consistency audit + animation data staged

- **Docs audit**: found and fixed a real contradiction in
  `40-vpk-mdl-pipeline.md` — its gotcha said "mip 0 is FIRST after the
  header", which is the exact bug fixed in 706c28b (mip chain is
  smallest-first; mip 0 is last). The note predates the fix and was written
  while the decoder was still wrong. Rewritten to match decisions.md.
- **Animation data staged** into the offline copy: `~/Documents/tf2/tf/
  models/player/<cls>_animations.mdl` for all 9 classes (44 MB). Recon
  notes for the future animation pipeline are in STATUS "next": TF2 player
  models are COMPOSITES — geometry in `<cls>.mdl`, sequences in
  `<cls>_animations.mdl` merged at load via $includemodel; TF2 v48 header
  offsets differ from the SDK layout (numlocalseq is NOT at 124; empirically
  numseq=20/seqindex=1028384 worked at 204/208 for soldier.mdl); seqdesc
  stride is 196 bytes per Crowbar v48 (34 ints + 15 floats), NOT the SDK's
  212. Sequence labels live in the _animations.mdl (e.g. ReloadStand_PRIMARY,
  layer_reload_standing_arms) — the base mdl only holds ~20 local seqs.
- **Textures fix NOT yet visually confirmed by the user** (Firefox check of
  http://192.168.101.10:5173/modelview.html?cls=medic&team=red pending).
  The [dbg] console.log line in modelview.html is still committed in the
  file — remove after user confirmation.
- Packed the offline copy for transfer: /tmp/opencode/tf2_19.tar.zst (128 MB,
  zstd -19) and tf2.tar.zst (150 MB, zstd -3).

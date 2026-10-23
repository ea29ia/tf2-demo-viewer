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

- **Lighting rework per user request (2026-10-23, later)**: baked light is OFF
  (`LIGHTMAP_INTENSITY = 0.0`, plumbing kept as a knob); the world is shaded by
  MeshStandardMaterial + sun/fill/hemi (2.6/0.85/2.1) with real shadows, and
  glTF `normalTexture` now maps to a real normalMap (21 materials). `$detail`
  is embedded and transported through the emissive slot + `extras.sourceDetail`
  (16 materials, all blendMode 0 MODULATE) and applied in the viewer via an
  onBeforeCompile modulate injection; WVT keeps its own emissive transport.
  The baked prop tint/instanceColor was removed (the previous blue cast came
  from scene lights, the white look from its removal — user is reviewing).
  Verified: full chain + `validate_glb.py` PASS (44830 tris, 21.8 MiB),
  `tsc --noEmit`/`npm run build` exit 0. HUMAN visual review in progress.

- **Prop meshes use baked light (2026-10-23)**: the instanced prop models were
  lit by the scene's blue hemisphere/fill while the world is baked unlit, so
  props looked blue. `add_props_glb.py` now stores the baked nearest-face tint
  in extras `"c"`; `propModels.ts` builds unlit MeshBasic materials and applies
  the tint per instance through `instanceColor` (sRGB -> linear; a
  `color_fragment` injection covers three versions where USE_INSTANCING_COLOR
  alone does not tint). BLEND props (light cones) stay untinted. Verified:
  full GLB chain + `validate_glb.py` PASS (44830 tris), 1182/1182 entries have
  `c`, `tsc --noEmit`/`npm run build` exit 0. Colour still needs a HUMAN look.

- **All static props are real meshes (2026-10-23)**: 225 of 234 placed models
  exported (9 skybox models intentionally excluded), 0 failures, manifest 225
  entries. Embedded prop textures are capped at 512 px (`--tex-size`, bilinear,
  premultiplied-alpha downscale) — the directory is 43.2 MiB instead of the
  ~100 MiB native-res estimate, and every GLB tops out at 512x512. The map GLB
  now omits **all 1182** prop boxes (props/foliage meshes are gone; only world +
  kits remain). Foliage props carry `"f": true` in extras; the viewer routes
  their instanced meshes into `state.foliageMeshes`, so the hide-foliage
  checkbox applies to real models too. Verified: GLB chain + `validate_glb.py`
  PASS (44830 tris, 21.2 MiB), `tsc --noEmit`/`npm run build` exit 0, scout
  player GLBs byte-identical. HUMAN visual check still required.

- **Prop mesh batch 2 — track/wood/window/train/scaffolding (2026-10-23)**:
  `props2glb.py` gained `--exclude`; 45 more models exported (16.8 MiB,
  0 failures, manifest = 66 entries / 36.9 MiB). Prop exports now carry
  Source alpha: `mdl2glb.py` keeps PNG alpha and sets `alphaMode` from the
  VMT in prop mode only (chicken_wire -> MASK 0.25; the window VMTs are
  genuinely OPAQUE in Source: `$basemapalphaphongmask`, no translucency).
  Player exports stay byte-identical (scout red/blue hashes match).
  `add_props_glb.py --manifest` now omits **462** boxes (140 boulders + 322
  of this batch). Verified: full GLB chain + `validate_glb.py` PASS
  (53470 tris, props mesh 8016 tris, 21.5 MiB), `tsc --noEmit` and
  `npm run build` exit 0. HUMAN visual check still required.

- **Real boulder meshes (2026-10-23)**: 21 `boulder_*` models exported by
  `tools/props2glb.py --match boulder` into `web/public/models/props/` (21 MiB,
  textures embedded) with `manifest.json`. `tools/add_props_glb.py` now stores
  the full instance transform in the props extras (`o/a/k/m`), reads the
  manifest, and omits the 140 boulder boxes; the viewer
  (`web/src/viewer/propModels.ts`) instances the real meshes by COLOR-free
  `M = T(o)·R·S(k)·C`. StaticPropLump flags are read from byte 31 (was 64 =
  DX level) and uniform scale from float 56. Verified: full GLB chain +
  `validate_glb.py` PASS (21.6 MiB, 57334 tris, props mesh 11880 tris,
  140 boxes omitted), `tsc --noEmit` and `npm run build` exit 0. Placement,
  orientation and scale still need a HUMAN visual check with
  `cd web && npm run dev`.

- **Source-fidelity map pass (2026-10-23)**: lightmap luxels are decoded as
  ColorRGBExp32 -> sRGB (was BGRA, red/blue swapped, exponent dropped), the
  grid is (size+1)^2 with stride size+1, displacement lightmap UVs use the
  base quad, and the atlas packer no longer overflows its 1px gutter. Brush
  faces are filtered by SURF_NODRAW/SKY/SKIP/HINT instead of material-name
  substrings. Alpha MASK/BLEND now comes from VMT flags and is preserved by
  the viewer when MeshStandard is converted to MeshBasic. WorldVertexTransition
  materials carry a second texture and blend by COLOR_0.r (displacement alpha);
  $bumpmap is embedded as a normal map (stored, not yet shaded). Props get a
  baked tint from the nearest lightmapped face. Verified: full GLB chain +
  `validate_glb.py` PASS (21.5 MiB, 59014 tris, 74 materials), `tsc --noEmit`
  and `npm run build` exit 0. Visual review (brightness, blending,
  transparency, prop shading) is a HUMAN task via `cd web && npm run dev`.

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
python3 tools/add_props_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb web/public/models/props/manifest.json
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

## Session update — 2026-10-23: map-render phase 1 (UV + per-material GLB)

Roadmap (user-approved, in docs/agent-notes/HANDOFF-style plan): textures →
lightmaps → real prop meshes → special materials → skybox. "No size limits"
and "all 234 prop models, gradually".

- **bsp2glb.py rewritten**: one primitive per used texdata material (upward:
  72) with TEXCOORD_0 from texinfo s/t vectors, vertex colors kept. Final GLB
  after add props+kits: 4 meshes (world/props/foliage/kits), 74 materials,
  58538 tris, 3.99 MiB, validate_glb.py PASS. UV verified NaN-free; the
  chessboard/texel-density check is still pending (next session).
- **add_props_glb.py / add_kits_glb.py rewritten**: append-only — new meshes
  at the end of the BIN, existing bufferViews/accessors untouched. extras.props
  stays on node 0 (1212 entries = 1182 props + 30 kits, same as before).
- **validate_glb.py rewritten**: validates ALL meshes/primitives (bbox over
  all POSITION accessors, index ranges, UV NaN check, per-mesh summary).
- **web/viewer/playback.ts**: loadMap() keeps GLTF materials (flatShading
  overlay + fallback); wireframe toggle handles material arrays. tsc + build
  exit 0; headless chromium renders the new GLB, [inspect] prop entries
  loaded: 1212, no page errors. USER eyeball round still pending (heuristic #7).
- **tools/vmt.py (new)**: KeyValues parser + patch resolver, case-insensitive
  keys (camelCase $baseTexture is everywhere in TF2 VMTs).
- **tools/texres.py (new)**: BSP→VMT→VTF resolver + report. upward: 123 VMTs
  resolved (pak=5, tf2_misc=118), 126/126 VTFs resolved (tf2_textures),
  56.5 MiB raw; 3 texdata names have no VMT (DEV/REFLECTIVITY_30,
  DEV/REFLECTIVITY_10B, water/water_well_beneath). Mip self-consistency on map
  textures: mean|diff| 1.01 (DXT1) / 1.70 (DXT5) — chain order verified.
- **tools/props2glb.py (new)**: sprp enumeration + top-N prop export via the
  mdl2glb pipeline. Top 5 (light_cone_farm_32 x80, light_fluorescent_basement
  x46, boulder_pile2 x43, oildrum x36, track_straight_256 x31) exported to
  /tmp/opencode/props — structurally valid, textures resolved, headless render
  OK. Scout regression: byte-identical to shipped scout_red.glb.
- **Corrections to older notes**: static-prop counts are 234 unique / 1244
  instances (sprp), NOT 88/123 (entity lump); scout GLB is 7814 tris, NOT 5120
  (figure predates the winding/origMeshVertID fixes).
- **Not done yet / next**: chessboard UV density check; embed resolved textures
  into the GLB (size policy: no hard limit per user; suggested top-20@512 /
  rest@256 then measure); lightmap atlas; prop InstancedMesh wiring in web.

## Session update — 2026-10-23 (later): player textures CONFIRMED by user
- User eyeballed modelview.html in Firefox: player textures are correct.
  This closes the pending verification from the texture-fix session; the
  [dbg] console.log line was removed from web/public/modelview.html in the
  same commit. No further texture-pipeline work is blocked.

## Session update — 2026-10-23 (evening): brush textures EMBEDDED in map GLB
- **Player textures confirmed by the user** (Firefox, modelview.html) — [dbg]
  log removed from modelview.html (commit 2eb3507). Texture pipeline closed.
- **tools/add_textures_glb.py (new)**: resolves each world material's VMT
  (BSP pak -> tf2_misc -> loose) and $basetexture VTF (pak -> tf2_textures ->
  loose), decodes mip 0, downscales (512^2 for materials with painted area
  >= 200k u^2, else 256^2), embeds PNGs into the GLB (images/textures/samplers
  appended, PNG bytes appended to BIN; geometry untouched). COLOR_0 of
  textured materials is neutralised to 1.0 IN PLACE (glTF multiplies vertex
  colors into base color; the palette stand-in would darken the real
  texture). KHR_materials_unlit for UnlitGeneric signs.
- **vtf.py**: added decode_vtf_scaled(data, max_size) (integer box downscale)
  + write_png_bytes. decode_vtf behaviour unchanged.
- **Result on upward**: 71/72 materials textured (only DEV/REFLECTIVITY_30
  has no VMT — stays vertex-grey), 70 unique PNGs, final GLB 16.23 MiB,
  validate_glb.py PASS (43994 + 13560 + 624 + 360 tris). UV texel scale
  verified against the BSP: |s|/|t| = 4 texels per world unit on 14662 faces
  (Source's 0.25 u/texel convention).
- **Verified in headless chromium**: map renders textured (wood fence,
  grass/dirt ground blend, brick/concrete walls), no page errors, [inspect]
  prop entries 1212. USER eyeball round on real Firefox still pending
  (heuristic #7) — check orientation of known signs and overall brightness.
- **Chain recipe is now five steps** (AGENTS.md recipe update pending):
  bsp2glb -> add_textures -> add_props -> add_kits -> validate. Order matters:
  add_textures right after bsp2glb (it only touches world materials).
- **Next**: lightmap atlas (phase 3), prop InstancedMesh (phase 4,
  props2glb.py ready), WorldVertexTransition blending (phase 5), skybox
  (phase 6). Runtime ~105 s for add_textures (70 PNG encodes, zlib -9).

## Session update — 2026-10-23 (night): map texture embedding FIXED end-to-end
Three bugs in add_textures_glb.py, all caught by browser verification after a
user-reported Firefox error ("json.textures is undefined"):
1. local `textures` list never assigned into gltf JSON (materials referenced
   textures with no textures array) — fixed;
2. bufferView cursor advanced by unpadded PNG length -> shifted image blobs
   (~50/70 "Couldn't load texture") — fixed (advance by aligned length);
3. KHR_materials_unlit not declared in extensionsUsed -> GLTFLoader crash
   "reading 'getMaterialType'" — fixed (declared in extensionsUsed).
validate_glb.py now verifies material texture wiring (baseColorTexture/
metallicRoughness/normal/emissive indices resolve through textures->images;
None = legal absence). All 70 embedded PNGs re-verified by byte-level parse
(sig + IHDR + IDAT inflate + raw size). Final: 16.23 MiB GLB, PASS,
chromium renders textured map, overlay hidden, [inspect] 1212, no errors.
USER Firefox eyeball pending (hard reload; signs RED/BLU orientation, seams).

## Session update — 2026-10-23 (night, final): user CONFIRMED textured map
User eyeballed the viewer in Firefox: textured pl_upward map is correct
(signs, seams, brightness all OK). Map-render phase 2 (brush textures) is
closed end-to-end. Next up per the approved roadmap: phase 3 = lightmap
atlas (BSP lump 8, 15110 faces, ~1.2M luxels -> 2048^2 atlas, TEXCOORD_1 +
lightMap on MeshStandardMaterial; 581 faces have no lightmap -> white),
phase 4 = real prop meshes via props2glb.py + InstancedMesh (234 unique
models / 1244 instances).

## Session update — 2026-10-23 (final): lightmap format decoded, implementation parked
- Map-render phases 1+2 shipped and user-confirmed (per-material GLB + UVs;
  embedded brush textures). Commits 76223d5, 2eb3507, 90cf79a, 828efa7.
- Phase 3 groundwork: BSP lightmap format fully decoded empirically on
  upward — see docs/agent-notes/60-lightmap-format.md (dface_t fields,
  4-byte BGRA luxels, contiguous rows, luxel-space projection via texinfo
  lm vectors, loose atlas allocation). Implementation (atlas + TEXCOORD_1 +
  web wiring) is the next session's first task; the note contains the sketch
  and open questions (gamma, displaced faces, unlit-vs-standard choice).
- Environment note: the OpenCode subagent free tier ran out mid-session;
  the four partially-run agents' output was verified by hand before use
  (all good), and the remaining work was done in the main session.

## Session update — 2026-10-23 (later): lightmaps SHIPPED (phase 3)
- bsp2glb.py: lightmap atlas 2048^2 (shelf-pack, 1px gutter, white 2x2 cell
  at origin for unlit faces), TEXCOORD_1 per vertex, <map>_lm.png sidecar
  (2.2 MiB). 13994 faces lightmapped. GLB 16.76 MiB, validate PASS.
- playback.ts: atlas -> material.lightMap (SRGBColorSpace — display-referred
  bytes; flipY=false), ONLY for meshes with a uv1 attribute (props/kits/
  foliage have none and stay sun-lit); world materials swapped to
  MeshBasicMaterial (baked light replaces direct lighting on the map);
  sun 2.6->1.4, fill 0.85->0.45, hemi 2.1->1.2 when baked.
- Verified: tsc 0, chromium renders dark interior vs warm sunlit yard;
  brightness stats old vs new: mean 13.7 -> 19.0, bright(>60) 0.6% -> 3.2%.
  USER eyeball pending (hard reload; look for seams at face borders, tunnel
  darkness, overall exposure).
- Implementation details/gotchas: see 60-lightmap-format.md (its "sketch"
  section is now implemented; gamma question resolved via SRGBColorSpace).

## Session update — 2026-10-23 (lightmap black screen FIXED)
User reported "all black" after the lightmap commit. Root causes (both in
playback.ts, fixed same commit area):
1. Texture.channel defaults to 0 -> lightMap sampled from tiled uv, not uv1
   (set lightMap.channel = 1);
2. three.js RECIPROCAL_PI dims the baked map 3.14x
   (set lightMapIntensity = Math.PI).
Post-fix headless stats: mean 13.7 -> 33.2, bright pixels 0.6% -> 12.0%.
Gotchas recorded in 60-lightmap-format.md. USER verification pending.

## Session update — 2026-10-23 (checkerboard FIXED, cause found)
User reported checkerboard brightness after the lightmap phase. Root cause:
make_lmap clamped the luxel projection to [0, size-1] instead of shifting by
dface_t lightmapMins (negative on most faces) -> affected faces collapsed to
a single texel -> uniform bright/dark patches alternating per face. Fixed by
subtracting mins; also reserved a proper 4x4 white cell at the atlas origin.
Rebuilt + validate PASS + chromium render OK. User verification pending.

## Session update — 2026-10-23 (latest): dark ground FIXED — $bumpmap height maps wired as normalTexture

User report: props fine, ground nearly black (displacements). Root cause found
by decoding the embedded PNGs: 12 of 21 `$bumpmap` textures are SSBump/HEIGHT
maps (`*_height-ssbump`, `*_bump-ssbump` — grayscale height fields for
Source self-shadowing), NOT tangent-space normal maps. `add_textures_glb.py`
wired them into `normalTexture` anyway; a grayscale height map as a normal
map bends tangent-space normals sideways/down (decoded means ~60/60/60
instead of blue-dominant) -> lambert ≈ 0 -> black ground. Vertical walls with
the same bug only lost some light, hence "ground dark, walls ok".

Fix: `add_textures_glb.py` attaches normalTexture only when the bump texture
name contains "normal" (Source convention); ssbump/height maps are skipped
with a note (12 skipped, 9 real normal maps attached). Real SSBump shading is
future work. GLB rebuilt FROM SCRATCH (bsp2glb -> add_textures -> add_props
-> add_kits; re-running add_textures on an already-textured GLB appends
duplicate PNGs — 35 MiB bloated intermediate caught and discarded).
Verified: `validate_glb.py` PASS (18.81 MiB, 59014 tris), ground WVT
materials now have no normalTexture, `rockwall001_normal` (B-mean 243) kept.
USER eyeball pending.

## Session update — 2026-10-23 (follow-up): gray boxes regression — manifest arg was dropped

User report after the ground fix: "some props are gray cubes again". Cause:
the from-scratch GLB rebuild ran `add_props_glb.py` WITHOUT the third
(manifest) argument, so all 1182 prop boxes were re-emitted, including the
462+ that real meshes replace — boxes render around/over the loaded meshes.
Fixed by re-running the full chain with
`web/public/models/props/manifest.json`: all 1182 boxes omitted again, GLB =
world + kits only (18.32 MiB, 44830 tris, validate PASS). AGENTS.md and the
STATUS validation recipe now carry the manifest arg explicitly.

## Session update — 2026-10-23 (night): open-.dem-from-browser (dev-only, pl_upward)

User request: pick a .dem file in the UI; upward-only for now. The browser
cannot parse .dem (WASM parser is parked in TODO.txt), so the implementation
is a dev-server helper:

- `web/vite.config.ts` (NEW): `POST /__dump_demo` middleware (dev AND
  preview servers). Body = raw .dem (cap 1 GiB), written to a mkdtemp dir,
  run through `preprocess/target/release/dem2replay`, meta.json checked for
  `map == pl_upward` (else HTTP 415 with the map name), replay.bin returned
  as application/octet-stream. Errors are JSON `{error, map?}`. The dumper
  must exist (`cargo build --release`), else 503 with instructions.
- `web/index.html`: "Открыть .dem…" pill button + hidden file input in the
  playback card. `dom.ts` exports demoFileInput/demoFileBtn.
- `web/src/viewer/playback.ts`: `loadDemoFile()` — POSTs the file, parses
  the returned replay.bin (map gated client-side too), then fully resets the
  previous session: setPlaying(false), hideAllViews(), dispose+remove every
  PlayerView group/material/label texture, views.clear(), selection null,
  camera mode free — then setupReplay() + resetFreeCamera. Button shows
  "Обработка…" while busy; the label is restored via applyLang().
- i18n keys: errDemo/errDemoDump/errOnlyUpward/openDemoBtn/openDemoBusy (RU+EN).
- Verified: tsc --noEmit + npm run build exit 0; live curl of the middleware
  with `soldier_upward.dem` (39 MB) → 43 MB replay.bin in ~4.8 s,
  `validate.py` OK (map pl_upward, 60480 frames); swiftwater demo → HTTP 415
  `unsupported map "pl_swiftwater_final1"`. USER eyeball pending: pick a
  .dem in the sidebar while `npm run dev` runs.

Scope note: production build serves static files only — the endpoint is not
shipped; the WASM in-browser parser (TODO.txt) is the real fix later.

# Handoff — session of 2026-10-22 (x86_64 desktop)

Read `../AGENTS.md` first, then `STATUS.md`, then this file. Everything below
was verified with real commands; nothing is "should work".

## Environment (IMPORTANT — changed from previous sessions)

- This is now an **x86_64 Linux desktop** (not the aarch64/Termux box).
  Node 24.22 (`~/.local/node`, symlinked into `~/.local/bin`) and Rust 1.99
  (`~/.cargo`) were installed this session. Python 3.12 system-wide.
- **TF2 is installed locally**: `~/.steam/debian-installation/steamapps/common/
  Team Fortress 2`. This means full VPK access (models, textures), maps, and
  demo recordings. The "blocked on VPK access" TODO items are now unblocked.
- Demos available in `tf/`: `demp.dem`, `demp_2.dem` (pl_upward),
  `pocket.dem`/`pocket1.dem` (pl_swiftwater_final1), `fix.dem` (pl_upward).
- Dev server running in background: `cd web && npm run dev` →
  http://127.0.0.1:5173.
- `gh` CLI was not needed this session; `git push` over https worked fine.

## What was done (chronological)

1. **Environment bootstrap**: installed Node + Rust, `npm install` in web/,
   `cargo build --release` for preprocess — all clean.
2. **Assets regenerated from local files** (no more fastdl):
   - `demp.dem` → `web/public/replay/replay.bin` (95349 frames, 1434 s,
     24 players, 487 kills, pl_upward, recorder LepeMot). `validate.py` → OK.
   - local `pl_upward.bsp` → map GLB. `validate_glb.py` → PASS.
3. **Bug fixed: 3D-skybox props leaked into the GLB** (bbox max x was 8898;
   floating boxes far from the map). `tools/add_props_glb.py` now culls prop
   origins against worldspawn `world_mins/maxs` intersected with the same sane
   constants `bsp2glb.py` uses. 1242 → 1182 entries, bbox now
   x[-3072, 4100.6]. Committed.
4. **Bug fixed: click-inspect loaded 0 props** (user-reported, never root-caused
   before). Cause: three.js GLTFLoader merges glTF `node.extras` into
   `object.userData` via `Object.assign`, so the code must read
   `userData.props`, not `userData.extras.props`. Fixed in
   `web/src/viewer/inspect.ts`. Verified live in a browser tab: console shows
   `[inspect] prop entries loaded: 1212`, synthetic clicks across the canvas
   produced info cards (barrels, rails, fences, boulders, lights). Committed.
5. **MAP_URL switched back** from cp_dustbowl to pl_upward
   (`web/src/viewer/state.ts`), tsc + build clean. Committed together with #4.
6. **All committed and pushed** as `845dadc fix(tools,web): cull 3D-skybox
   props; fix click-inspect extras lookup`. Working tree clean except the new
   uncommitted VPK/MDL tools (below) and `web/public/replay/meta.json`
   (regenerated data, gitignored apart from meta — check .gitignore intent).

## What was started but NOT finished (the current task)

User chose the next task: **real meshes from VPK** (option 1 of 4). Work done
so far — three new tools, all stdlib-only, uncommitted:

- `tools/vpk.py` — VPK v1/v2 reader. WORKING (listed 104137 entries in
  tf2_misc_dir, extracted scout.mdl/vvd/vtx correctly).
- `tools/vtf.py` — VTF decoder (DXT1/DXT3/DXT5, BGR/BGRA/BGRX) + PNG writer.
  WORKING (scout_red 2048x1024 and barrel01 512x512 decode to correct images).
- `tools/mdl2glb.py` — MDL v48 + VVD + dx90 VTX → textured GLB, bind pose,
  skin families 0/1 → red/blue outputs. WORKING for scout: 8261 LOD0 verts,
  5120 triangles, correct team texture embedded (scout_red/scout_blue). Output
  in `/tmp/opencode/tf2/glb/scout_{red,blue}.glb` (tmp — regenerate).

**Remaining known gap**: eyeball meshes use the `EyeRefract` shader (no
`$basetexture`); the `$Iris` fallback was just added and needs re-verification
(eyes may still render without material).

**Not yet done**: no visual verification in the browser (load
`/tmp/opencode/tf2/glb/scout_red.glb` in `web/modeltest.html` or a scratch
three.js page), no export of the other 8 classes, no replacement of the
dribble.tf DRACO set in `web/public/models/players/`, no wiring changes in
`playerModels.ts`.

## Where the knowledge is

- **`docs/agent-notes/40-vpk-mdl-pipeline.md`** — the full field-offset and
  format gotcha list for VPK/VTF/MDL/VVD/VTX with verified numbers. READ THIS
  before touching `mdl2glb.py`; every number there cost debugging time.
- `STATUS.md` — updated with a session block (env change, fixes, validation).
- `decisions.md` — two new appended decisions (userData.props, skybox culling).

## Verify-before-done commands (this environment)

```sh
cd web && npx tsc --noEmit && npm run build          # web changed
cd .. && python3 preprocess/validate.py web/public/replay/replay.bin \
    web/public/replay/meta.json                      # replay changed
python3 tools/validate_glb.py web/public/maps/pl_upward.glb  # map changed
python3 tools/mdl2glb.py "$TF" models/player/scout /tmp/x/scout  # models
```

## Recommended next steps

1. Verify scout GLB visually (three.js scratch page or modeltest.html).
2. Fix eyeball `$Iris` loading; re-export scout.
3. Export all 9 classes × 2 teams into `web/public/models/players/`
   (replacing dribble.tf set — also removes the DRACO CDN dependency and the
   MvM robo-pyro), update `playerModels.ts` (new GLBs are not Draco).
4. Then prop meshes (top models by instance count), then map textures.
5. Commit `tools/vpk.py`, `tools/vtf.py`, `tools/mdl2glb.py` with a proper
   message once the pipeline is verified end-to-end in the browser.

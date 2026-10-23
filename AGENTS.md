# TF2 Demo Viewer — Agent Guide

Pipeline: TF2 `.dem` → Rust dumper (`preprocess/`) → `replay.bin` + `meta.json`,
BSP → GLB (`tools/`), browser viewer (three.js, `web/`). Everything runs locally;
assets from Valve are NOT committed — see `tools/get_map.sh` and TODO.txt.

## Layout

```
viewer/
  PLAN.md            data-format contract (replay.bin layout, meta.json)
  TODO.txt           postponed features (models, real kets, cart, VPK assets)
  preprocess/        Rust crate `dem2replay`: demo -> replay.bin v2 + meta.json
    src/main.rs      dumper (players/projectiles/hurts per tick)
    validate.py      byte-level replay.bin validator (run after any change)
  tools/             python, stdlib-only converters
    bsp2glb.py       BSP v20 -> GLB (brushes, displacements, vertex colors)
    probe_props.py   static-prop lump parser (finds entry stride etc.)
    add_props_glb.py oriented prop boxes + foliage split + extras metadata
    add_kits_glb.py  health/ammo kit pads + kit extras
    get_map.sh       downloads maps from public fastdl mirrors
    validate_glb.py  GLB sanity check (tri count, bbox) — must PASS
  web/               Vite + TypeScript + three.js viewer
    vite.config.ts   dev/preview middleware: POST /__dump_demo (.dem ->
                     dem2replay -> replay.bin; pl_upward gate; dev-only)
    src/main.ts      scene, cameras, playback, FX
    src/replay.ts    replay.bin parser (the ONLY place that knows the binary layout)
    src/playerModels.ts  player glb loading (DRACO), per-instance material clones
    src/i18n.ts      RU/EN strings, t()/setLang()/applyLang()
  web/modeltest.html standalone model-loading test scene (/modeltest.html)
```

## Hard rules

1. **Comments and identifiers in English.** No Russian in code comments. User-facing
   strings are localized via `i18n.ts` (both languages live in STRINGS).
2. **The binary format is a contract.** Any change to replay.bin goes through:
   update PLAN.md section → update `preprocess/src/main.rs` AND `web/src/replay.ts`
   in the same commit → bump VERSION → regenerate → run `validate.py`. The web
   parser must accept the version it declares; do not ship mismatched writers/readers
   (this caused a real bug once: writer emitted 29-byte records, reader read 24).
3. **Verify before claiming done.** Every change must end with, in the terminal:
   - preprocess changed: `cargo build --release` + run dumper + `python3 validate.py`
   - tools changed: regenerate GLB + `python3 tools/validate_glb.py` (must print PASS)
   - web changed: `npx tsc --noEmit && npm run build` (both exit 0)
   Paste the actual output into the report. No "should work".
4. **Visual checks are for humans, not headless browsers.** Never use a headless
   browser (screenshots, Puppeteer/Playwright, `browser.preview` of rendered
   pages, headless Chrome) to inspect maps, textures, lighting, models, UI or
   any other image output. Agents must not judge visuals from machine-rendered
   screenshots. Instead: produce the artifact (GLB, atlas PNG, replay), state
   the exact file path and the command that opens it (`npm run dev`), and ask
   the human to look and report back. Automated numeric checks
   (`validate_glb.py`, `validate.py`, `tsc`/`build`) stay mandatory; they do
   not replace the human visual review.
5. **Coordinate conventions.** Source is Z-up; the GLB keeps Source coordinates;
   the scene rotates the map by `-PI/2` X and maps `(x,y,z) -> (x,z,-y)`. Any new
   geometry or lookup must use that mapping or it will be silently misplaced.
   The click-inspect metadata (`extras.props`) stores Source-space positions and is
   converted in `collectPropEntries`.
6. **No Valve assets in git.** `.bsp`, `.glb`, `replay.bin` are gitignored; commit
   only code and small data (kits.json is fine — it's coordinates).
7. **Windows/paths:** two dev boxes are in use. The aarch64 Android (Termux)
   box needs `preprocess/.cargo/config.toml` (clang linker + `-L/system/lib64`)
   — already set; do not remove it (it only matches the
   `aarch64-linux-android` target, so it is inert on the x86_64 Linux desktop,
   which has Node/Rust installed under `~/.local/node` and `~/.cargo`).
   On the desktop, TF2 is installed under
   `~/.steam/debian-installation/steamapps/common/Team Fortress 2`, so maps,
   demos and VPKs are available locally.

## Regeneration recipes

Map GLB from scratch:
```
python3 tools/bsp2glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb
python3 tools/add_textures_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb
python3 tools/add_props_glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb web/public/models/props/manifest.json
python3 tools/add_kits_glb.py web/public/maps/pl_upward.glb web/public/maps/kits.json
python3 tools/validate_glb.py web/public/maps/pl_upward.glb
```
(add_textures_glb.py needs the local TF2 install for VPK textures; it
appends images to the GLB and neutralises vertex colors on textured
materials — run it right after bsp2glb, BEFORE add_props_glb.py.
bsp2glb.py also writes the lightmap atlas sidecar
web/public/maps/pl_upward_lm.png — it is gitignored with the GLB and must
be regenerated together with it.)

Replay from a demo:
```
preprocess/target/release/dem2replay demo.dem web/public/replay
python3 preprocess/validate.py web/public/replay/replay.bin web/public/replay/meta.json
```

Dev server: `cd web && npm run dev` (port 5173). `npm run build` output goes to
`web/dist/` and includes public assets.

## Agent coordination

**Always delegate implementation to subagents after computing file
collisions.** Before dispatching, list the files each work item touches, count
the overlaps (code files, generated artifacts such as the map GLB/atlas/dist,
and shared interfaces), give every agent a disjoint file set, reserve the
canonical artifacts and docs for the lead, and state the interface contracts
explicitly in the prompts. Parallel agents must never regenerate
`web/public/maps/pl_upward.glb` or `_lm.png`; they use `/tmp/opencode/` copies.

Before starting a task, read `docs/agent-notes/STATUS.md` and any note in
`docs/agent-notes/` tagged with your task area (see its README.md for the note
format and rules). After finishing work, update `STATUS.md` in the same commit
and append to `docs/agent-notes/decisions.md` for any non-obvious decision.
Write in English (project-wide rule).

## Conventions

- TypeScript strict; no new runtime deps in web without discussion.
- Python tools stay stdlib-only (lzma/struct/array are fine).
- Player colors: team is ground truth (red `#e03c3c`, blue `#2f7bff`); spies are
  purple with a true-team ring; cloaked spies are translucent.
- Flags in replay records: bit0 alive, bit1 in_pvs, bit2 disguised, bit3 stealthed.

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
    src/main.ts      scene, cameras, playback, FX
    src/replay.ts    replay.bin parser (the ONLY place that knows the binary layout)
    src/i18n.ts      RU/EN strings, t()/setLang()/applyLang()
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
4. **Coordinate conventions.** Source is Z-up; the GLB keeps Source coordinates;
   the scene rotates the map by `-PI/2` X and maps `(x,y,z) -> (x,z,-y)`. Any new
   geometry or lookup must use that mapping or it will be silently misplaced.
   The click-inspect metadata (`extras.props`) stores Source-space positions and is
   converted in `collectPropEntries`.
5. **No Valve assets in git.** `.bsp`, `.glb`, `replay.bin` are gitignored; commit
   only code and small data (kits.json is fine — it's coordinates).
6. **Windows/paths:** the dev box is aarch64 Android (Termux). Rust builds need
   `preprocess/.cargo/config.toml` (clang linker + `-L/system/lib64`) — already set;
   do not remove it.

## Regeneration recipes

Map GLB from scratch:
```
python3 tools/bsp2glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb
python3 tools/add_props_glb.py web/public/maps/pl_upward.glb assets/pl_upward.bsp
python3 tools/add_kits_glb.py web/public/maps/pl_upward.glb
python3 tools/validate_glb.py web/public/maps/pl_upward.glb
```

Replay from a demo:
```
preprocess/target/release/dem2replay demo.dem web/public/replay
python3 preprocess/validate.py web/public/replay/replay.bin web/public/replay/meta.json
```

Dev server: `cd web && npm run dev` (port 5173). `npm run build` output goes to
`web/dist/` and includes public assets.

## Conventions

- TypeScript strict; no new runtime deps in web without discussion.
- Python tools stay stdlib-only (lzma/struct/array are fine).
- Player colors: team is ground truth (red `#e03c3c`, blue `#2f7bff`); spies are
  purple with a true-team ring; cloaked spies are translucent.
- Flags in replay records: bit0 alive, bit1 in_pvs, bit2 disguised, bit3 stealthed.

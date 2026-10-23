# Handoff — session of 2026-10-22/23 (x86_64 desktop → phone development)

Read `../AGENTS.md` first, then `STATUS.md`, then the tagged notes
(`40-vpk-mdl-pipeline.md`, `decisions.md`). Everything below was verified
with real commands unless marked otherwise.

## Environment

- x86_64 Linux desktop. Node 24 (`~/.local/node`), Rust 1.99 (`~/.cargo`),
  Python 3.12 system-wide. Playwright-core installed in /tmp/opencode
  (chromium-1248, firefox-1555 in ~/.cache/ms-playwright) — used for
  headless visual checks (`/tmp/opencode/shot*.mjs` patterns).
- TF2 installed locally: `~/.steam/debian-installation/steamapps/common/
  Team Fortress 2/tf`. Offline subset for phone work: `~/Documents/tf2/`
  (328 MB: tf/models, tf/materials, tf/models/player/*_animations.mdl,
  demos/). `tools/vpk.py` `open_pack()` accepts the loose tree.
- Dev server: `cd web && npx vite --host 0.0.0.0 --port 5173`
  (user accesses via http://192.168.101.10:5173). May not be running.
- Packed transfer archives: `/tmp/opencode/tf2_19.tar.zst` (128 MB,
  zstd -19) and `/tmp/opencode/tf2.tar.zst` (150 MB, zstd -3).

## State of the player-model work (the main thread of this session)

All 9 classes × 2 teams are real VPK-derived GLBs in
`web/public/models/players/` (gitignored? NO — they are committed; ~9 MB).
Export command (works from the offline copy too):

```sh
python3 tools/mdl2glb.py ~/Documents/tf2/tf models/player/<cls> \
    web/public/models/players/<cls>   # demoman dir: models/player/demo
```

Three fixes landed this session, in causal order:

1. **f03389e winding**: Source VTX strips are D3D CW; glTF needs CCW.
   mdl2glb emits (a,c,b). Verified: geometric-vs-vertex normal agreement
   3129/3139 on the medic head (was 10/3139). Without this every mesh
   rendered inside-out (dark faces, "texture on the wrong body part").
2. **71108fd TextureLoader path**: `window.createImageBitmap = undefined`
   before GLTFLoader in `web/src/playerModels.ts` and `web/public/
   modelview.html` — the ImageBitmapLoader path ignores texture.flipY at
   upload and browsers disagree on bitmap orientation (Firefox mirroring).
   tsc + vite build exit 0; dbg log confirms src=HTMLImageElement.
3. **706c28b THE ROOT CAUSE — vtf.py mip order**: VTF stores the mip chain
   smallest-first; mip 0 (full res) is the LAST mip_size bytes. We decoded
   from the start → spatially SCRAMBLED textures for every material. This
   is what produced "эмблема на бедре", "кожа на животе", "чёрное лицо" —
   all of it. Decisive test: downscale decoded mip0 by 2 and compare to
   decoded mip1 — mean|diff| 1.0 (correct order) vs 41.4 (old). After the
   fix medic face verts sample skin at 59-70% (was 5%), avg (167,102,75).
   All 18 GLBs regenerated (f3de42e).

IMPORTANT CONTEXT: earlier UV-flip A/B conclusions (V-flip added in
e23c5e3, reverted in de916fb) were computed against the CORRUPTED decoder
output and must not be trusted as evidence. Current state: NO V flip,
D3D top-left origin == glTF convention.

## UNRESOLVED / awaiting user

- **User has NOT yet visually confirmed the texture fix in Firefox.**
  Ask him to hard-reload http://192.168.101.10:5173/modelview.html?cls=
  medic&team=red. The `[dbg]` console.log line in modelview.html is still
  committed — remove after confirmation.
- The "F" (Flip V) button on modelview.html now serves as a diagnostic:
  with correct data, pressing F should BREAK the texture; if it FIXES it,
  something else is off.
- Models face running direction: only math-verified, not eyeballed in motion.

## Animation work (staged, not started in code)

- `~/Documents/tf2/tf/models/player/<cls>_animations.mdl` copied for all
  9 classes (user request; nothing parses them yet).
- Recon findings (verified today, see STATUS 2026-10-23):
  - TF2 player models are COMPOSITES: `<cls>.mdl` (geometry/materials) +
    `<cls>_animations.mdl` (sequences), merged via $includemodel at load.
    An exporter must read both.
  - TF2 v48 header offsets DIFFER from SDK studio.h: numlocalseq is not at
    offset 124; empirically for soldier.mdl numseq=20/seqindex at 204/208.
    Verify per-field before trusting.
  - mstudioseqdesc_t stride = 196 (Crowbar v48: 34 ints + 15 floats), NOT
    the SDK's 212 (Crowbar omits bbmin/bbmax read but they are present...
    treat 196 as hypothesis to re-verify against real data).
  - Sequence names live in the _animations.mdl: ReloadStand_PRIMARY,
    AttackStand_PRIMARY, layer_reload_standing_arms, PRIMARY_aimmatrix_idle
    etc. Base mdl holds only ~20 local seqs (ragdoll, ragdollspawn,
    user_ref, ACT_DIERAGDOLL...).
  - Path not yet found: pointers from seqdesc to label strings did not
    match any obvious offset convention in probing — resolve offsets
    empirically (label strings at known abs offsets in soldier_animations:
    'ragdoll'@1044250, 'user_ref'@1044273, 'ACT_DIERAGDOLL'@1044258).
- Also present (not copied): `<cls>_user_animations.mdl` (taunts).

## Consistency audit (this session)

- Fixed contradiction in `40-vpk-mdl-pipeline.md`: it said "mip 0 is FIRST"
  — that was the BUG, written while the decoder was wrong. Now matches
  decisions.md (smallest-first, mip 0 last).
- STATUS.md offline-copy size corrected 287 → 328 MB (animations added).
- TODO.txt/README checked: no other contradictions found. TODO already
  lists "player animations (m_nSequence)" as the residual item.

## Verified commands (this session)

- `npx tsc --noEmit` + `npm run build` → exit 0 (after 71108fd).
- medic GLB from loose tree vs VPK: JSON identical, same byte size.
- Headless chromium renders medic with skin-colored face post-fix
  (bm_face3.png crop); Firefox headless could not create WebGL context
  (software-GL prefs failed) — user's real Firefox check still pending.

## Suggested next steps

1. Get user's visual confirmation of the texture fix (blocking cleanup of
   [dbg] and any further texture work).
2. Animation pipeline: parse _animations.mdl (seqdesc stride 196, animdesc,
   mstudioanim_t quarter/half quantized tracks), add bones + skinning +
   glTF animations for idle/run/crouch; wire m_nSequence→anim selection in
   the viewer (sequence/cycle are not yet in replay.bin — PLAN.md change
   required per the hard rule before touching the binary format).
3. Prop meshes via the same converter (top-20 models by instance count).

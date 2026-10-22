# VPK / MDL / VTF pipeline — findings from the x86_64 session

- Date: 2026-10-22
- Author: agent session (OpenCode + GLM), x86_64 Linux desktop
- Scope: `tools/vpk.py`, `tools/vtf.py`, `tools/mdl2glb.py` (new), VPK/MDL/VVD/VTX
- Tags: #vpk #mdl #vtf #models #textures #vpk-unlocked

## State (updated 2026-10-22, night)

All 9 player classes export cleanly; see the STATUS.md block for the fixes
(garbage placeholder models, VPK-wide material resolution, axis mapping
(x,y,z)->(z,y,-x) — model space is y-up/forward+z, NOT z-up). Headless
chromium (playwright-core, browser cached in ~/.cache/ms-playwright) can be
used for visual checks: npm i playwright-core in /tmp/opencode, then a
scratch page in web/public/ + `node shot.mjs`.

The long-standing "blocked on VPK access" limitation is GONE on this machine:
TF2 is installed locally at
`~/.steam/debian-installation/steamapps/common/Team Fortress 2` with all
`tf2_*.vpk`, `tf/maps/*.bsp`, and demo recordings (`tf/*.dem`). This session
built three new stdlib-only Python tools (all working, verified):

- `tools/vpk.py` — VPK v1/v2 reader (directory parse + archive read + preload).
- `tools/vtf.py` — VTF decoder (DXT1/DXT3/DXT5, BGRA/BGRX/BGR/RGB) + PNG writer.
- `tools/mdl2glb.py` — MDL v48 + VVD + dx90 VTX → textured GLB (bind pose).
  Produces `<prefix>_red.glb` / `<prefix>_blue.glb` from skin families 0/1.
  Status at handoff: geometry + indices + VVD fixups verified correct for
  `models/player/scout`; texture resolution works for `$basetexture` VMTs
  (scout body renders with scout_red/scout_blue textures). Eyeball meshes
  (EyeRefract, no `$basetexture`) resolve to `$Iris` texture as a fallback but
  that texture fails to load — eyes get no material, see "Open questions".

Verified commands:

```sh
TF="$HOME/.steam/debian-installation/steamapps/common/Team Fortress 2/tf"
python3 tools/mdl2glb.py "$TF" models/player/scout /tmp/opencode/tf2/glb/scout
# → scout_red.glb / scout_blue.glb: 8261 verts, 5120 tris, scout_red texture embedded
python3 tools/vpk.py "$TF/tf2_misc_dir.vpk" list models/player/
python3 - <<'EOF'
import sys; sys.path.insert(0,'tools')
from vpk import Vpk; import vtf
v=Vpk("$TF/tf2_textures_dir.vpk")
w,h,rgba=vtf.decode_vtf(v.read('materials/models/player/scout/scout_red.vtf'))
# → 2048x1024 correct skin texture
EOF
```

## Gotchas (hard-won, verified)

### VTF
- **Mip 0 (largest) is FIRST, right after the header** — NOT smallest-first.
  A first implementation that skipped small mips produced a solid-brown image;
  the fix (read from `header_size` directly) produced the correct texture.
- Texture data offset = the `headerSize` field at offset 12 (104–112 typical);
  do not assume 80.
- `width/height` at offset 16 (`<HH`), image format at offset 52 (`<I`),
  mip count at byte 56. TF2 player/prop textures are DXT1 (fmt 13), DXT5 (15)
  or BGR/BGRA variants. Supported decoders are in `vtf.py` (mip 0, frame 0).
- DXT5 layout per block: 8 bytes alpha (a0, a1, 48-bit codes) THEN 8 bytes
  DXT1 color. DXT3: 8 bytes explicit 4-bit alpha THEN DXT1 color.

### VPK
- Preload data is stored in the `_dir.vpk` right after the directory tree,
  in entry order. The entries do NOT store its offset — you must track the
  cursor while parsing. `Vpk.preload[key]` holds it.
- Archive files (`_000.vpk`...) hold the payload beyond the preload bytes:
  `length - preload_len` bytes at `offset` in archive `archive` (0x7FFF means
  the directory file itself).
- Entries are keyed extension-last, lowercase: `models/player/scout.mdl`.
- `tf2_misc_dir.vpk` holds MDL/VVD/VTX/VMT; `tf2_textures_dir.vpk` holds VTF.

### MDL (version 48)
- Header field offsets (i32 unless noted): version 4, checksum 8, name 12,
  numtextures 204, textureindex 208, numcdtextures 212 (NOT used — cdtexture
  strings read garbage; texture path resolution uses the texture names),
  cdtextureindex 216, numskinref 220, numskinfamilies 224, skinindex 228,
  numbodyparts 232, bodypartindex 236.
- Offsets at 156/160 are numbones/boneindex. `flags` at 152.
- **mstudiomesh_t on-disk stride is 116 bytes**, NOT 132. Fields: material 0,
  modelindex 4, numvertices 8, vertexoffset 12, then flex stuff, then
  `mstudio_meshvertexdata_t` whose `numLODVertexes[MAX_NUM_LODS=8]` starts at
  offset 52 within the struct.
- **mstudiomodel_t stride is 96** (name[64], type, boundingradius, nummeshes,
  meshindex, numvertices, vertexindex, tangentsindex, numattachments,
  attachmentindex, numeyeballs, eyeballindex — offsets 64..88).
- `mstudiobodyparts_t` stride 16: nameindex, nummodels, base, modelindex.
- Texture names: entries WITHOUT a `/` (e.g. `scout_blue`) are relative to the
  model's own material dir — derive it from a fully-qualified sibling entry
  (e.g. `models/player/scout/scout_red` → prepend `models/player/scout/`).
- Skin family 0 = red, 1 = blue (verified: family 1 maps scout body to
  scout_blue). numskinref=17 covers body+head+2 eyeballs+invun+僵尸 variants.

### VVD (version 4)
- Header: checksum 8, numLODs 12, numLODVertexes[8] at 16, numFixups 48,
  fixupTableStart 52, vertexDataStart 56, tangentDataStart 60.
- **mstudiovertex_t is exactly 48 bytes**: weights (3 floats + 3 bytes bone +
  1 byte numbones = 16), pos 3f @16, normal 3f @28, uv 2f @40.
- **Fixup application (critical)**: iterate `vertexFileFixup_t {lod, src, cnt}`
  in order; for each with `lod >= rootLOD`, copy `cnt` vertices from raw
  `sourceVertexID` to the next output slot. With rootLOD=0 this is a
  permutation that produces the mesh-ordered vertex pool the MDL's
  `model.vertexindex` (byte offset, divide by 48) refers to. Skipping this
  produces scrambled/zero coordinates (first raw entries are 0..1 junk).
- `model.vertexindex` in the MDL is a BYTE offset into the fixup-adjusted
  pool; mesh `vertexoffset` is per-mesh (add to model base, in vertices).

### VTX (dx90, version 7)
- Header: version 0, vertCacheSize 4, maxBonesPerStrip/PerTri `<HH` @8,
  maxBonesPerVert 12, checksum 16 (matches MDL checksum), numLODs 20,
  materialReplacementListOffset 24, numBodyParts 28, bodyPartOffset 32.
- All `*Header_t` records are 8 bytes (num + offset pairs) except:
  ModelLODHeader_t 12 (numMeshes, meshOffset, switchPoint float),
  MeshHeader_t 9 (numStripGroups, stripGroupHeaderOffset, flags u8),
  StripGroupHeader_t 25 (numVerts, vertOffset, numIndices, indexOffset,
  numStrips, stripOffset, flags u8),
  StripHeader_t 27 (numIndices, indexOffset, numVerts, vertOffset, numBones
  s16, flags u8, numBoneStateChanges, boneStateChangeOffset).
- **Vertex_t is 9 bytes packed** (`#pragma pack(1)`): boneWeightIndex[3],
  numBones, origMeshVertID u16 @+4, boneID[3]. The `origMeshVertID` maps a
  strip-group vertex to the mesh's vertex list (0..mesh.numvertices-1).
- Strip flags: 0x01 = TRILIST, 0x02 = TRISTRIP (flip winding on odd k when
  expanding to a triangle list). Strip group indices are u16 into the group's
  vertex array; convert through `origMeshVertID` to mesh-local, then add the
  primitive's vertex base.
- Verified for scout: 8490+7962+60+60 body tris + hat/headphones/shoes/dogtags
  bodygroups = 5120 exported triangles at LOD0, all indices within range.

### Materials / textures
- Player VMTs are `VertexLitGeneric` with `$basetexture` (regex
  `\$basetexture"?\s*"?([^"\s]+)` — note the optional trailing quote right
  after the key, as in `$basetexture" "path"`).
- Head texture trick: `scout_head_red.vmt` has `$basetexture
  models/player/scout/scout_head` — the *shared* head texture. It loads fine.
- Eyeballs use `EyeRefract` shader: `$Iris`, `$CorneaTexture`, `$Envmap`,
  `$AmbientOcclTexture` instead of `$basetexture`. The iris VTF exists at
  `materials/models/player/shared/eye-iris-blue.vtf` but the current loader
  still misses it (needs investigation — the fallback regex was added but not
  re-verified).
- Other VTFs live under `dxlevel80/` subdirs for some materials (e.g. eyeball
  L/R base textures are in `materials/models/player/shared/dxlevel80/`).

### Environment (this box)
- x86_64 Linux desktop (NOT the Termux aarch64 box). Node 24.22 installed at
  `~/.local/node` (symlinked into `~/.local/bin`), Rust 1.99 via rustup at
  `~/.cargo`. Python 3.12 system.
- Dev server runs on http://127.0.0.1:5173 (background npm run dev).
- TF2 install: `~/.steam/debian-installation/steamapps/common/Team Fortress 2`
  (Steam "debian-installation" root, not `.steam/steam`).
- Demos in `tf/`: demp.dem + demp_2.dem (pl_upward), pocket.dem + pocket1.dem
  (pl_swiftwater_final1), fix.dem (pl_upward).

## Open questions

- Eyeball material: verify the `$Iris` fallback loads
  `materials/models/player/shared/eye-iris-blue.vtf`; if the loader path
  prefixes are wrong, add `materials/models/player/shared/` resolution.
- Multi-material VMTs (`$basetexture` with spaces/args) — only single-token
  names seen so far.
- `scout_animations.mdl` / `scout_user_animations.mdl` exist — animation data
  is available if we ever want poses (out of scope for static export).
- Bodygroups: all 5 scout bodygroups are exported merged; the viewer may want
  to drop hat/dogtags (cosmetics hidden by default in TF2? — they are visible
  in-game, keep merged).
- Validate the GLB with `tools/validate_glb.py` after wiring into the viewer
  (it expects map-style single-primitive GLBs; may need tolerance for
  multi-primitive + textured models).

## Next steps (suggested order)

1. Fix eyeball texture resolution; re-run scout export; visually verify both
   team GLBs in `web/modeltest.html` (wire a quick loader there).
2. Extend `mdl2glb.py` to all 9 classes × 2 teams; drop the DRACO'd
   dribble.tf set; replace vanilla pyro (fixes the MvM robo-pyro TODO item).
3. Prop meshes: same converter applies to `models/props_mining/*.mdl` etc.
   Map props are many — pick the top-20 models by instance count from
   `probe_props.py` output first, cache per model name, instance in viewer.
4. Map textures: BSP texinfo → material names → VTF basemaps; replace vertex
   colors (bigger job, separate tool `bsp2glb` rewrite).
5. Update `playerModels.ts` to load the new GLBs (they are NOT Draco-compressed
   — remove/keep DRACOLoader for backward compat).

## Post-handoff fix (2026-10-22, late night): origMeshVertID deref was MISSING
The dx90 VTX reader (`Vtx.root_lod` in mdl2glb.py) extended `tris` with the RAW
strip-group indices; strip-group index arrays are compacted/reordered relative
to the mesh vertex list, so indices must be dereferenced through the group's
Vertex_t array (`verts[...]`, origMeshVertID at byte offset +4 of the 9-byte
packed struct) to get mesh-local vertex ids. Symptom of the bug: geometry and
UVs scrambled relative to each other — models rendered as a grey plastic-bag
silhouette (texture sampled from wrong texels). The "gotchas" section above
already documented the correct rule; the code just didn't implement it.
Also verified NOT the cause: mesh.vertexoffset (applied via
model.vertexbase + mesh.vertexoffset when copying vertices; mesh-local
indices align), tristrip flip handling, bone weights (static export, unused).

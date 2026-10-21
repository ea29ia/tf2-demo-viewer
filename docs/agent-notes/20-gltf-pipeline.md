# GLTF map pipeline

- Date: 2026-10-21
- Author: lead agent
- Scope: `tools/*.py`, `web/public/maps/`
- Tags: #gltf #map #props

## State

`pl_upward.bsp` (v20) → `pl_upward.glb` pipeline works and is validated. Chain:
`bsp2glb.py` (brushes + displacements, vertex colors) → `add_props_glb.py`
(oriented prop boxes, foliage split, extras metadata) → `add_kits_glb.py`
(kit pads + kit extras) → `validate_glb.py` (must print PASS).

## Gotchas

- **BSP lumps are LZMA-compressed** (`LZMA` magic inside the lump). `Bsp::lump()`
  in `bsp2glb.py` handles it; raw file offsets cannot be used directly for such
  lumps.
- **Game lump (35) sprp data**: the `sprp` entry's `offset` is an ABSOLUTE file
  offset, and the data starts with its own LZMA blob. Layout after decompress:
  model_count i32 → names at fixed 128-byte stride → leaf_count i32 + u16 leaves
  → prop_count i32 → prop entries at **stride 72** (verified by exact fit:
  35476 + 1244×72 = 125044). Prop angles are (pitch, yaw, roll) — roll matters
  for rails on slopes; the box transform must use the full QAngle matrix
  (R = Rz·Ry·Rx).
- **Vertex colors, not textures.** Palette by material-name keywords
  (wood/metal/concrete/rock/grass...). Tools stay stdlib-only.
- **Foliage** (grass/bush/shrub/weed/... keywords) goes into a separate mesh
  node named `foliage`; the viewer hides it by default ("Скрывать кусты/траву").
- **Kit positions** come from the BSP entity lump (lump 0), class names
  `item_healthkit_*` / `item_ammopack_*` / `func_regenerate`; saved to
  `web/public/maps/kits.json` (30 entries with origin).
- **Click-inspect metadata**: `nodes[0].extras.props = [{n,p,s}, ...]` in
  SOURCE-space coordinates (Z-up); conversion to scene space happens in
  `collectPropEntries` (`(x,y,z) -> (x,z,-y)`). Do not store scene coords in GLB.
- `add_kits_glb.py` must preserve `nodes[0].extras.props` written by
  `add_props_glb.py` (it appends kit entries instead of overwriting).
- World-bounds guard: props outside x/y ±12000, z [-2500, 3500] are skipped
  (3D-skybox strays would destroy the bbox and fail validate_glb.py).

## Open questions

- Real prop meshes (MDL) are blocked on VPK access; boxes stay for now.
  `probe_props.py` is the reference for parsing the sprp lump.

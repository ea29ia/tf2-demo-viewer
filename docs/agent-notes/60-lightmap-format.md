# Lightmap format in TF2 BSP v20 (decoded, not yet implemented)

- Date: 2026-10-23
- Author: agent session (OpenCode + GLM)
- Scope: `tools/bsp2glb.py` (future lightmap atlas), BSP lumps 8/53
- Tags: #lightmap #bsp #map #verified

## State

Format fully decoded empirically on pl_upward; renderer integration NOT
started. Next session can implement `add_lightmaps_glb.py` / bsp2glb support
directly from this note.

## Verified facts (all on assets/pl_upward.bsp, 15691 faces)

- **dface_t field layout (v20, 56 bytes, our FACE_STRUCT tuple indices):**
  - `f[12]` (int at byte 22) = `lightofs`, BYTE offset into lump 8. `-1` for
    unlit faces (styles all 255): 581 faces, rest 15110 have lightmaps.
  - `f[14], f[15]` (ints at bytes 30/34) = `lightmapMins[2]` (can be NEGATIVE —
    grid extends half a luxel past the face edges).
  - `f[16], f[17]` (ints at bytes 38/42) = `lightmapSize[2]` in luxels
    (0 < size <= 512 on upward; max seen 112x66).
- **Storage: 4 bytes per luxel, NOT 3.** Lump 8 = 12,411,440 = 4 × 3,102,860.
  Row layout is CONTIGUOUS: `luxel(i,j) at lightofs + (j*size_x + i)*4`.
  Decisive test (smoothness oracle, 50-heuristics #1): horizontal-neighbor
  mean|diff| 69.2 for (stride=4, row=w*4) vs 237+ for stride=3 — lightmaps are
  smooth gradients, noise means wrong stride.
- **Byte order: BGRA** (byte0=B, byte1=G, byte2=R, byte3≈250/251 constant).
  Outdoor upward ground samples ~RGB(177,155,129) — warm, matches TF2 sunlight.
  Use lump 8 (LDR); lump 53 (HDR) same size, same layout, float-free.
- **Projection: texinfo lightmap vectors ARE luxel-space projection.**
  `u = dot(P, lm_s) + lm_s.shift`, `v = dot(P, lm_t) + lm_t.shift`
  (TEXINFO_STRUCT floats 8..15). Vertex u-range spans ~[mins_x, mins_x+size_x-1];
  clamp u/v to [0, size-1] per face — half-luxel misalignment is invisible.
- **Atlas allocation is loose**: sum(size_x*size_y)*4 = 4.79 MB < lump 12.4 MB;
  lightofs values have gaps (block allocation). Do NOT try to chain offsets —
  just read each face's rect at its own lightofs.
- Atlas for the viewer: ~1.2M luxels → 2048² fits with 1px gutters
  (15110 faces: shelf-pack rects of size_x×size_y).

## Implementation sketch (next session)

1. bsp2glb.py: shelf-pack all lightmap rects (+1px gutter) into 2048²; write
   TEXCOORD_1 per vertex = `(cellx + u_frac)/2048, (celly + v_frac)/2048`;
   unlit faces → point at a reserved 1x1 white cell; write atlas PNG
   `<glb-dir>/<name>_lm.png` (BGRA→RGB, gamma = store as sRGB).
2. Web: fetch the atlas as a SIDECAR texture (do not embed in GLB — GLTFLoader
   has no lightmap slot; assigning after load avoids parser hacks):
   `new THREE.TextureLoader().load('maps/pl_upward_lm.png')` → for world
   materials set `material.lightMap = tex; material.lightMapIntensity = 1`.
   Decision pending: MeshBasicMaterial (fully baked, cheapest, loses dynamic
   shadows on map) vs MeshStandardMaterial + lightMap (keeps sun for
   players/props but double-lights the map unless sun intensity is cut).
   Suggested: convert world materials to MeshBasic (map + lightMap) and keep
   the directional sun ONLY for props/players.
3. validate_glb.py: extend UV NaN check to TEXCOORD_1.

## Open questions

- Gamma handling: assume lightmap bytes are display-ready sRGB (LDR BSP);
  if the baked map looks washed out/dark vs the game, try linear→sRGB or
  2.2 pow on decode. Verify with a user screenshot round.
- Displaced faces: lightmap projection is linear over the BASE quad; sampling
  displaced vertices with the same formula is approximate (validate visually).
- `worldLightDistance`/style fields beyond style 0 ignored (switchable lights).

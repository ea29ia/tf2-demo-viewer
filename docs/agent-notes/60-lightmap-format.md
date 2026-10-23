# Lightmap format in TF2 BSP v20 (decoded, not yet implemented)

- Date: 2026-10-23
- Author: agent session (OpenCode + GLM)
- Scope: `tools/bsp2glb.py` (future lightmap atlas), BSP lumps 8/53
- Tags: #lightmap #bsp #map #verified

## State

IMPLEMENTED (2026-10-23, see STATUS): bsp2glb.py packs lightmap rects into a
2048^2 atlas (1px gutters, white 2x2 unlit cell), emits TEXCOORD_1, and
writes `<map>_lm.png` next to the GLB; playback.ts binds it as
material.lightMap (sRGB, flipY=false) on meshes with uv1 and swaps world
materials to MeshBasicMaterial (baked light replaces direct lighting).
Format findings below remain the reference for other maps / rework.

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

## Gotchas added at implementation (2026-10-23, black-screen bug)

- **three.js `Texture.channel` defaults to 0.** Without
  `lightMap.channel = 1` the lightmap samples from uv (tiled TEXCOORD_0,
  range -4..4), not uv1 — random atlas lookups, mostly the black background:
  the whole map goes black/patchy. This is the #1 suspect for any
  "all black after lightmaps" report.
- **three.js multiplies the lightmap by RECIPROCAL_PI (1/pi)** in
  meshbasic/meshstandard shaders (lambert indirect normalisation). Source
  bakes the final display value, so set `lightMapIntensity = Math.PI` to
  undo the 3.14x darkening; without it the map is ~3 stops too dark.
- Headless chromium showed NO error for both bugs (just a dark render);
  the user's report was the only reliable signal. Numeric brightness stats
  of screenshots (mean / %pixels>60) did move measurably and are a cheap
  proxy: pre-fix 13.7/0.6%, post-fix 33.2/12.0%.

## Gotcha added 2026-10-23 (checkerboard bug — the big one)

- **The luxel projection spans [mins, mins + size - 1], NOT [0, size-1].**
  dface_t lightmapMinsInLuxels (f[14], f[15]) is the block origin in luxel
  space and is frequently NEGATIVE (the grid extends half a luxel past the
  face edges). The atlas-relative coordinate is u - mins_x / v - mins_y.
  Clamping to [0, size-1] instead of shifting collapses every vertex of such
  a face to ONE texel — the face renders as a uniform patch, some patches
  bright / some dark => the "checkerboard brightness" the user reported.
  Verified before the fix: u-span fit [mins, mins+size-1] on 73/73 faces
  with mins < -5; u_in_zero count 0.
- Also: pack_lightmaps now reserves a WHITE_CELL=4 texel white square at the
  atlas origin; the old 2x2 cell overlapped the first packed rect's first
  texel, so unlit faces sampled that face's lumel.
- Dark atlas cells (~55% of faces) are LEGITIMATE: back-sides of brushes
  (facing into the void, never visible in-game) and enclosed interiors bake
  black. Do not "fix" them.

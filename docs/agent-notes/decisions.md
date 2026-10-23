# Decisions log (append-only)

Format: `YYYY-MM-DD | decision | why | rejected alternative`


- 2026-10-21 | Preprocess demos offline with a Rust CLI instead of parsing in the browser | tf-demo-parser is proven and fast; browser gets a compact replay | WASM parsing in browser (parked in TODO, revisit later)
- 2026-10-21 | replay.bin: fixed-size binary records, little endian, meta JSON embedded | streaming-friendly, ~35MB for 14min demo, trivially validatable | JSONL (2-3x bigger), protobuf (overkill)
- 2026-10-21 | Map as pre-converted GLB with vertex colors, no textures | no Valve asset redistribution, works offline, small (4.6MB) | runtime BSP parsing in browser (heavy, textures need VPK access anyway)
- 2026-10-21 | Static props as oriented boxes with per-type sizes/colors | real MDL meshes blocked on VPK access; boxes preserve layout and orientation | empty map (unusable), sketchfab one-off models (incomplete set)
- 2026-10-21 | Player models from dribble.tf glb set (civilian pose) | only complete 18-piece browser-ready set found; DRACO-compressed | extracting MDLs ourselves (blocked on VPK), keeping capsules (user rejected)
- 2026-10-21 | Spies: purple body + true-team ring; cloaked = translucency | demo team prop shows the FAKE team for disguised spies (verified 49k records); ring communicates the real team without a second model | coloring by fake team (misleading), question-mark sprite (ugly at distance)
- 2026-10-21 | Damage numbers from player_hurt events, not computed | authoritative server values incl. crit/mini-crit flags | estimating from health deltas (misses crits, overkill)
- 2026-10-21 | Splash radius from a weapon_id table, not from demo | TE/radius data not sent for explosions; standard TF2 radii are known | parsing particle configs (fragile, overkill)
- 2026-10-21 | Explosive projectile disappearance spawns a splash ring | player_hurt splash flags are incomplete (direct hits, blocked damage); disappearance is reliable | only using hurt-based splash (user reported missing sticky splash)
- 2026-10-21 | Follow camera defaults to the demo recorder (meta.nick) | the recorder is the demo owner; most common "watch me" flow | first player in list (random-feeling)
- 2026-10-21 | Foliage hidden by default via separate GLB node | foliage clutters tactical reading; cheap to toggle | per-prop transparency (GPU cost), omitting from GLB (loses the toggle)
- 2026-10-21 | Wall-collision raycast for follow camera | capsule cameras clipping into walls made the map unreadable | navmesh-based (way too complex), no fix (user complained)
- 2026-10-21 | RU/EN i18n with localStorage persistence, detect from navigator.language | user asked; two languages suffice | server-side detection (no server), per-URL (noisy)
- 2026-10-21 | AGENTS.md hard rule: format changes = writer+reader+PLAN.md in one commit | a writer/reader mismatch (29B vs 24B projectile records) shipped once and broke everything silently | informal "be careful" guidance
- 2026-10-21 | docs/agent-notes/ as agent coordination layer with strict format | fresh agents were re-exploring the codebase for narrow tasks | single STATUS.md only (too coarse), full docs site (overkill)
- 2026-10-21 | Kit positions extracted from BSP entity lump into kits.json instead of tracking kit entities in the demo stream | kits are static map data, not runtime entities; one extraction serves all demos | parsing CTFItem/CTFAmmoPack entities per demo (wasted effort, same result)
- 2026-10-21 | Player view-direction arrows on capsules (later carried over to models) | "who is holding which angle" was unreadable from capsules alone | rotating a texture on the capsule (not visible from all angles)
- 2026-10-21 | Alive/dead counters computed from lastPoses, time-remaining shown as negative countdown in the total-time slot | user asked; avoids adding a second HUD row on mobile | separate scoreboard panel (clutter)
- 2026-10-21 | Spy disguise detected by comparing record team vs roster team; flags bit2/bit3 added to player record for disguised/stealthed conditions | m_iTeamNum reflects the fake team for disguised spies; conditions carry ground truth | shipping fake-team as real (would color spies by disguise)
- 2026-10-21 | Damage numbers: white normal / yellow crit, mini-crits also yellow (flagged separately in data) | matches TF2 in-game colors closely enough for MVP | distinct mini-crit color (visual noise at MVP)
- 2026-10-21 | Click-inspect metadata stored in GLB node extras (Source-space), not in a separate JSON | single artifact to ship; survives kit-script rewrite because add_kits_glb.py appends | sidecar JSON (two files to keep in sync)
- 2026-10-21 | Click-inspect ray fallback: nearest prop to the RAY when the geometry hit has no nearby prop | clicks landing on terrain next to thin props missed otherwise | requiring exact geometry hit (brittle)
- 2026-10-21 | DRACOLoader wired in both test scene and main viewer (decoder from gstatic CDN 1.5.7) | dribble.tf models are Draco-compressed; without the loader every load silently fell back to capsules | re-encoding models uncompressed (extra pipeline step, bigger files)
- 2026-10-21 | Separate /modeltest.html scene with per-model status lines | user reported "nothing shows"; isolated scene turned a guessing game into one screenshot | debugging inside the full viewer (too many variables)
- 2026-10-21 | PVS coverage measured from replay.bin (max in-PVS distance 5092u, median out 889u) instead of trusting forum answers | POV demo visibility is leaf-based PVS, not a radius; direct measurement is authoritative | citing community rules of thumb
- 2026-10-21 | Sniper aim statistics designed and parked in TODO.txt (replay v3: shots block + headshot bit) | high-value feature but requires format bump; user deprioritized | implementing immediately inside v2 (format churn)
- 2026-10-21 | docs/agent-notes with mandatory note header format + disposable notes + append-only decisions log | agents were re-exploring the codebase for narrow tasks; strict format keeps notes current and deletable | free-form wiki (rotten notes), single fat STATUS (too coarse)
- 2026-10-21 | validate.py extended: hurt-block parsing added, flag mask widened to 0x0F | validator was written for v2-without-hurts and rejected legit new flag bits; a stale replay.bin surfaced the gap | regenerating data without fixing the validator (gap would recur)
- 2026-10-21 | Dumper suppresses frame emission until the first tick-0 packet (signon phase) and consumes hurt events via a forward cursor | POV demos can start with signon packets carrying absolute server ticks below header.ticks, which started the replay timeline at 63467 and then reset to 0 (backwards timeline); tf-demo-parser never clears state.events, so a full scan per tick was O(n) and desynced on long demos | skipping by `tick > header.ticks` only (works by luck when signon tick exceeds header.ticks), mutating parser state (borrowed, impossible)
- 2026-10-22 | replay.bin v3: player health u8 -> u16 (record 26 -> 27 bytes) + event-based health sourcing in the dumper | heavy spawns at 300 HP (u8 clamped to 255); m_iHealth sendprops only arrive in-PVS so out-of-PVS players froze at stale values, and PVS re-entry resurfaces a stale per-entity instance baseline after respawn (identical wrong constant after every respawn, confirmed against kill events); player_hurt events are global and carry exact remaining health | clamping to resource max_health (resource m_iMaxHealth is garbage for some players: 1, 70) or trusting the respawn prop value (stale baseline)
- 2026-10-22 | Health audit (tools/health_audit.py) uses the parser's Class enum ids (scout 1, sniper 2, soldier 3, ..., engineer 9) and gates respawn windows on kill events | the analyser class ids match TF2 TF_CLASS_*; a table assuming 2=soldier/5=heavy mislabels classes and reports fake "wrong health" plateaus (a "heavy at 150" was actually a correct medic); without the kill gate, stale life state makes every PVS re-entry look like a respawn | trusting the diagnosis table as given
- 2026-10-22 | VTF mip 0 is stored first, right after the header (largest-first) | the "skip small mips" assumption produced a solid-brown image; reading from headerSize produced the correct texture | keeping smallest-first layout (matches older VTF docs but not reality)
- 2026-10-22 | MDL mesh stride is 116 bytes and model stride 96 (v48, 32-bit layout) | 132-byte assumption decoded garbage meshes; verified against source-sdk-2013 studio.h struct layout | guessing strides from data
- 2026-10-22 | VVD vertices must be reordered through the fixup table exactly like Studio_LoadVertexes (lod>=rootLOD, sequential output slots) | raw vertex order is LOD-sorted, not mesh-ordered; without fixups positions are scrambled 0..1 junk | trusting raw order
- 2026-10-22 | Convert MDL→GLB at bind pose without bones | the viewer only needs static player/prop meshes; skinning adds rigging complexity for no visual gain at capsule scale | full skinning export (needs animation system first)
- 2026-10-22 | Click-inspect reads `userData.props`, not `userData.extras.props` | three.js GLTFLoader merges glTF `node.extras` into `object.userData` via `Object.assign`, so the nested key never existed and the lookup silently loaded 0 props | reading the raw GLTF JSON separately (duplicate source of truth)
- 2026-10-22 | Static props are culled against worldspawn world_mins/maxs intersected with the old sane constants (same rule as bsp2glb.py) | 3D-skybox props sit thousands of units outside the playable world; the loose -12000..12000 box let ~90 of them into the GLB (visible floating boxes, bbox max x 8898) | filtering by "skybox" in the model name (misses boulder6/boulder7 in the skybox area)

## 2026-10-22 — mdl2glb model-space orientation (y-up, not z-up)
The handoff assumed MDL/VVD data is Source Z-up. It is not: player VVD vertex
pools have up=+y, forward=+z, left=+x (proved by the eyeball pair: eyeball_l
at +x, both eyes at +z). The GLB mapping is therefore (x,y,z)->(z,y,-x), which
both keeps Y-up (glTF) and makes the model face local +x — the direction the
viewer needs because it applies group.rotation.y = yaw with forward
(cos yaw, 0, -sin yaw). If future prop models look "lying down", suspect a
different model-space convention in their VVD before touching the viewer.

## 2026-10-22 — bsp2glb ear-clip triangulation (the "geometry explosion")
The spike/shard artifacts in the map mesh were NOT props or player models:
add_flat() fan-triangulated brush faces whose polygons carry near-duplicate
and collinear vertices from brush plane splits, emitting ~21k zero-area
slivers whose long edges stick out of surfaces (5670 needle triangles
catalogued by tri-level analysis). Fix: clean_polygon() (dedupe <0.05u,
drop collinear mids) + ear_clip() on the dominant plane, zero-area ears
skipped. upward: 65136 -> 43994 base tris (57914 with props+kits).
validate_glb.py min-tri threshold recalibrated 50000 -> 35000 — if a future
build again emits slivers the count RISES, so the low threshold still
catches catastrophic under-generation. Diagnostics worth keeping: raycast
via headless chromium + triangle-level AABB scan of the scene mesh (see
/tmp/opencode/needles.py pattern in the 40- note if needed).

## 2026-10-22: VTF mip order was the root cause of "wrong textures"
- VTF mip chain is stored smallest-first; mip 0 = LAST mip_size bytes of
  image data. vtf.py used to read from the start (thumbnails + head of
  larger mips) producing a scrambled image. Decisive test: downscale
  mip0 by 2 and compare with mip1 — mean|diff| 1.0 (correct order) vs
  41.4 (old order). After the fix, medic face verts sample skin (59-70%
  skin-rate, avg (167,102,75)); before: 5%, avg (28,27,28).
- Consequence: earlier UV-flip A/B conclusions were computed against
  corrupted textures and should not be trusted; no V flip is needed
  (D3D top-left origin = glTF).
- Also fixed same day: D3D CW -> glTF CCW winding flip in mdl2glb
  (geometric vs vertex normal agreement 3129/3139 after fix).
- Web: window.createImageBitmap=undefined before GLTFLoader forces the
  TextureLoader path (ImageBitmapLoader ignores texture.flipY at upload;
  browser orientation disagreements mirror islands). [dbg] log still in
  modelview.html — remove when confirmed.

## 2026-10-23: SUPERSEDED entry above (mip order)
The 2026-10-22 one-liner "VTF mip 0 is stored first, right after the header
(largest-first)" is WRONG and is superseded by the 2026-10-22 detailed entry
below it ("VTF mip order was the root cause of wrong textures"): the chain is
smallest-FIRST, mip 0 (full res) is the LAST mip_size bytes of image data.
Append-only log, so the wrong line stays — trust this entry and vtf.py's
comment, not the one-liner.

## 2026-10-23: map GLB = one primitive per texdata material + TEXCOORD_0
bsp2glb.py now emits one primitive per used texdata name (upward: 72) with
TEXCOORD_0 derived from the texinfo s/t vectors (u = (P·s + shift)/texW; no
V flip — Source texel origin already matches glTF). Materials carry the
texdata name in extras. Why: per-material textures need per-material
primitives, and tiling rules out atlasing. Rejected: keeping the single
primitive (cannot bind maps per surface); atlas (breaks REPEAT tiling).

## 2026-10-23: props/kits appended as separate GLB meshes, never rewritten
add_props_glb.py / add_kits_glb.py now APPEND "props"/"foliage"/"kits" meshes
at the end of the BIN chunk and only add bufferViews/accessors. Why: the
world mesh is now multi-primitive (72); the old rewrite-everything approach
would have destroyed it. extras.props stays on node 0 (click-inspect
contract), kits append to it. Invariants verified: bufferViews in bounds,
indices < vertex counts, world total 67310 verts / 43994 tris unchanged.

## 2026-10-23: VMT keys parse case-insensitively
TF2 map/prop VMTs frequently spell keys in camelCase ("$baseTexture" —
10,130 of 25,914 VPK VMTs). The case-sensitive regex silently dropped
$basetexture and fell back to coincidental MDL texture names. vmt.py now
lowercases keys at parse time; props2glb's PropTextures regex is
re.IGNORECASE. Rejected: leaving mdl2glb's shared regex case-sensitive
(player exports currently byte-identical; changing it needs its own
verified pass).

## 2026-10-23: sprp lump is the authoritative static-prop source
pl_upward: 234 unique models / 1244 sprp instances (vs 88 unique / 123 refs
in the entity lump — that only lists prop_* entities). All prop planning
counts come from probe_props.py's sprp parse, not the entity lump.

## 2026-10-23: brush textures embedded as per-material PNGs (no atlas)
Tiling textures rule out atlasing (REPEAT wrap needed), so each texdata
material gets its own PNG inside the GLB: 512^2 for the high-area materials
(painted area >= 200k u^2), 256^2 for the rest — 16.2 MiB GLB, user set no
hard size limit. Rejected: 1024 native (56.5 MiB raw, >100 MiB GLB), atlas
(breaks tiling), serving loose PNGs (two round trips, cache discipline).

## 2026-10-23: lightmap atlas as PNG sidecar + MeshBasic world materials
GLTF has no lightmap slot, so the atlas ships as <map>_lm.png next to the
GLB and is assigned to material.lightMap after load (only meshes with a uv1
attribute). World materials become MeshBasicMaterial: the baked light IS the
map's lighting — direct lights would double-light it, and basic materials
ignore them by definition (props/players keep the sun). Rejected: embedding
the atlas in the GLB (no glTF slot, parser hacks), MeshStandard+lightMap
(needed manual sun disabling anyway, kept PBR cost for a fully baked mesh).

## 2026-10-23: Source-fidelity pass (lightmap, WVT, alpha, prop light)
Verified against the open Source SDK (`bsplib.cpp`, `radial.cpp`,
`lightmap.cpp`, `color_conversion.cpp`, `builddisp.cpp`,
`lightmappedgeneric_*`). Adopted: ColorRGBExp32 decode (mantissa * 2^e / 255,
then sRGB encode) with a byte LUT; (size+1)^2 luxel grid with stride size+1;
displacement lightmap UVs interpolated on the base quad; alpha MASK/BLEND from
VMT flags; WVT blend weight in COLOR_0.r from CDispVert alpha.
Rejected: reading luxels as BGRA (old code: red/blue swap + lost exponent,
mean sRGB 163/139/118 instead of ~119/119/118); keeping MeshStandard+sun for
baked props (double lighting). The atlas stays 2048 with a 1px gutter; the
packer fit test now accounts for the gutter (previously 6 cells ended at
texel 2048 = one past the row and wrapped).
WVT transport hack: the second texture travels in glTF `emissiveTexture`
with `emissiveFactor = 0` and `extras.sourceBlend`; MeshBasic ignores
emissive, and the viewer reads it as the blend map. Rejected: a custom glTF
extension (GLTFLoader would not load the texture), unused texture index in
extras (index is not resolvable to a three texture after load).
Prop lighting is an approximation: nearest lightmapped face's avg-light
(`lightofs - 4`), using m_LightingOrigin only when STATIC_PROP_USE_LIGHTING_ORIGIN
is set; no occlusion or normal weighting.
Known gaps: $detail/$blendmodulatetexture, normal maps are stored but not
shaded (MeshBasic has no normal slot), water/sky shaders, SURF_WARP shading.

## 2026-10-23: real boulder meshes via per-model manifest
21 boulder_* models of pl_upward are exported once with props2glb.py
(`--match boulder`) into web/public/models/props/ (21 MiB, textures embedded,
no DRACO) plus `manifest.json` mapping full model id -> basename. The map GLB
keeps a compact extras entry for every prop and additionally carries the
instance transform (`o` base origin, `a` QAngle, `k` uniform scale, `m` model
id); add_props_glb.py omits box geometry for models present in the manifest.
The viewer instances each model as one InstancedMesh per primitive:
M = T(o) * Rz(yaw)*Ry(pitch)*Rx(roll) * S(k) * C, C: (X,Y,Z)_export ->
(-Z,Y,X)_source, because prop VVDs are Source Z-up (up = export +X), unlike
player models. Rejected: baking instance transforms into the GLB (huge),
box-only fallback (what the user complained about).
Fixed along the way: StaticPropLump flags are the byte at offset 31 (offset
64 is the DX level; the old code only "found" 5 lighting origins by
coincidence — the real count is 254, 222 of the emitted props) and
m_flUniformScale is the float at offset 56.
Known: prop models are sun-lit, boxes were baked-unlit; the extras box
size/center for exported props is still the keyword heuristic, so the click
tolerance and the bbox overlay are approximate for large boulders.

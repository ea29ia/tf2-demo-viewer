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

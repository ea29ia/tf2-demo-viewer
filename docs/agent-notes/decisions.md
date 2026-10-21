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

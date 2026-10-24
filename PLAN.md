# TF2 Demo Browser Viewer — MVP Plan

## Goal

Local browser app: load a TF2 `.dem`, render `pl_upward` in 3D, watch the fight from a
free camera. Players are capsules colored by team/class with name + health labels.
This is a POV demo, so entity data is limited to what the recorder's PVS contained.

## Layout

```
viewer/
  assets/pl_upward.bsp          # source map (not committed)
  preprocess/                   # Rust: .dem -> replay.bin + meta.json
  tools/bsp2glb.py              # BSP v20 -> GLB (vertex colors)
  web/                          # Vite + TS + three.js
    public/maps/pl_upward.glb
    public/replay/replay.bin
    public/replay/meta.json
  docs/
```

## Stage A — Rust dumper (`preprocess`)

Input: `<demo.dem> <out_dir>`.
Output: `<out_dir>/replay.bin`, `<out_dir>/meta.json`.

Use `tf-demo-parser` 0.7.1 + `GameStateAnalyser`. Tick loop via `DemoTicker`.
Snapshot whenever `state.tick` changes. Include only players whose team is red/blue.

`meta.json`:
```json
{
  "map": "pl_upward", "nick": "LepeMot",
  "duration": 837.63, "ticks": 55842, "frames": 51075,
  "intervalPerTick": 0.015, "tickRate": 66.6667, "startTick": 79,
  "users": [{"entity":3593,"name":"sfsasha5","userId":3593,"steamId":"[U:1:...]","team":"red"}],
  "kills": [{"tick":952,"attacker":3803,"victim":3778,"assister":3792,"weapon":"persian_persuader"}]
}
```

`replay.bin` (little endian), **version 4** (current):
```
magic        4 bytes  "TF2R"
version      u32      = 4
meta_len     u32
meta         meta_len bytes UTF-8 JSON (same content as meta.json)
frame_count  u32
frames[frame_count]:
  tick            u32
  player_count    u16
  player_count records (27 bytes each):
    entity   u16
    team     u8     2=red 3=blue
    class    u8     1..9 (scout..engineer), 0=other
    flags    u8     bit0 alive, bit1 in_pvs, bit2 disguised, bit3 stealthed
    health   u16    (v3: was u8 in v2; heavy spawns at 300 > 255)
    x,y,z    f32 x3   Source units
    yaw      f32      degrees (m_angEyeAngles[1])
    pitch    f32      degrees (m_angEyeAngles[0])
  projectile_count u16
  projectile_count records (17 bytes each):
    entity   u16
    team     u8     2=red 3=blue
    kind     u8     0 rocket, 1 arrow, 2 sticky, 3 pipe, 4 flare,
                    5 loose cannon, 7 unknown
    flags    u8     bit0 critical
    x,y,z    i16 x3  quantized Source units / 4
    vx,vy,vz i16 x3  quantized initial velocity / 4
  hurt_count      u16
  hurt_count records (15 bytes each):
    victim   u16    user id (meta.users.userId namespace)
    attacker u16    user id
    damage   u16
    flags    u8     bit0 crit, bit1 mini-crit, bit2 all-see-crit
    x,y,z    i16 x3  quantized Source units / 4 (victim position)
    splash   i16     quantized / 4; !=0 marks explosion damage with this
                    radius (by weapon id: rocket/grenade/sticky/sentry 146,
                    flare 110)
  building_count  u16   (v4)
  building_count records (15 bytes each; note: the field list below sums
                  to 15, an earlier draft said 20):
    entity   u16    server entity id of the object
    team     u8     2=red 3=blue
    kind     u8     0=sentry 1=dispenser 2=teleporter_entrance 3=teleporter_exit
    flags    u8     bit0 building(in-construction), bit1 sapped, bit2 mini
    level    u8     1..3 (0 while constructing)
    health   u8     quantized health/maxhealth * 255
    angle    u16    quantized yaw: degrees/360 * 65535
    x,y,z    i16 x3  quantized Source units / 4
  kit_count       u16   (v4)
  kit_count records (10 bytes each):
    index    u16    static kit index; 1:1 with the entries of
                    web/public/maps/kits.json (array order = index)
    kind     u8     0=health_small 1=health_medium 2=health_full
                    3=ammo_small 4=ammo_medium 5=ammo_full
    flags    u8     bit0 present (picked-up kits have bit0=0)
    x,y,z    i16 x3  quantized Source units / 4
```

Notes:
- One frame is emitted per demo tick; ticks without packets repeat the last
  snapshot so the timeline is contiguous.
- `startTick` in meta is `header.signon`; frames with tick > header.ticks
  (absolute sign-on ticks) are skipped.
- Health sourcing (v3): `m_iHealth` sendprops only arrive for entities in the
  recorder's PVS, so out-of-PVS players would freeze at a stale value. The
  dumper overrides health with the victim's remaining health from global
  `player_hurt` events whenever the event is more recent than the last observed
  prop change; dead players are always reported 0 (the sendprop often still
  reads 1 while dead).
- Buildings (v4) come from the analyser's `GameState.buildings` (cleared on
  RoundStart by the analyser itself, removed on entity deletion). The dumper
  caps the per-frame list at 32 (u16 count would allow more; the cap keeps
  frames bounded).
- Kits (v4) are the map's static health/ammo pickups. The analyser does not
  track them, so the dumper keeps the placements from `--kits <kits.json>`
  (same file the GLB chain writes; array order defines the index) and flips
  bit0 off when an `item_pickup` game event names the matching kit kind near
  the picker. Respawn times: small 10 s, medium 20 s, full 30 s (health and
  ammo alike). Without `--kits` the kit block is emitted with count 0.
  Player-dropped medkits (`item_pickup` strings starting with `medkit`) are
  intentionally ignored: they are not BSP kit placements.

Acceptance: `dem2replay demo.dem out` exits 0; on `demo_upward1.dem`:
55842 frames, max players 24, max projectiles 12; `validate.py` prints OK.

## Stage B — Map converter (`tools/bsp2glb.py`)

Input `assets/pl_upward.bsp`, output `web/public/maps/pl_upward.glb`.
Source BSP v20. Build render geometry from world model faces (models[0]):
vertexes, edges, surfedges, faces, texinfo. Include displacements (LUMP_DISPINFO /
DISP_VERTS) so terrain is not full of holes. No external textures: assign a vertex
color from a palette keyed by material name substring (wood/metal/concrete/rock/
grass/dirt/blend/glass/water).
Output one GLB mesh with POSITION, NORMAL, COLOR_0, indices. Y-up not required;
keep Source Z-up and convert in three.js (rotate -90° X). Acceptance: GLB parses;
triangles > 50,000; bounding box roughly x/y within ±10,000 and z within [-2,000, 3,000].

## Stage C — Web viewer (`web`)

Vite + TypeScript + three.js (no React needed). Load GLB + replay.bin.
- Map: vertex-colored mesh, fog, sky-ish background.
- Players: capsule (CapsuleGeometry) colored by team (red/blue), tint by class,
  billboard label with name + HP; dead/hidden (in_pvs=false) dimmed or hidden toggle.
- Playback: play/pause, timeline scrub, speed 0.1–4x, interpolate positions between ticks.
- Camera: free-fly (WASD + QE, drag mouse), follow selected player (3rd person),
  POV camera (player eye), orbit fallback.
- Side panel: player list (team, class, hp), click to follow; killfeed from meta.kills.
Acceptance: `npm run build` exits 0; `npm run dev` serves; no console errors; map visible.

## Non-goals (this iteration)

Player models/animations, textures/lightmaps, props, sound, WASM drag-and-drop,
multi-demo support beyond pl_upward, exact animation state.

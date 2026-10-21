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

`replay.bin` (little endian):
```
magic        4 bytes  "TF2R"
version      u32      = 1
meta_len     u32
meta         meta_len bytes UTF-8 JSON (same content as meta.json)
frame_count  u32
frames[frame_count]:
  tick       u32
  count      u16
  count records (26 bytes each):
    entity   u16
    team     u8     2=red 3=blue
    class    u8     1..9 (scout..engineer), 0=other
    flags    u8     bit0 alive, bit1 in_pvs
    health   u8
    x,y,z    f32 x3   Source units
    yaw      f32      degrees (m_angEyeAngles[1])
    pitch    f32      degrees (m_angEyeAngles[0])
```

Acceptance: `cargo run -- demo.dem out` exits 0 on `demo_upward1.dem`; frames > 50,000;
max players per frame >= 20; every position inside [-12000, 12000]; prints a summary.

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

# tf2-dem-eye

> **⚠️ AI slop warning / Предупреждение**
> **This project is built with AI ("AI slop").** Every line here — the Rust parser wrapper,
> the BSP converter, the three.js viewer, this README — was written by an AI agent
> (OpenCode + GLM) working with a human operator who clicks "done" and reports bugs
> like "the rails are crooked sticks". Manage your expectations accordingly.
>
> **Этот проект пишется с помощью ИИ ("AI slop").** Всё здесь — обёртка над Rust-парсером,
> конвертер BSP, three.js-вьюер и этот README — написано ИИ-агентом, а человек только
> жмёт «готово» и репортит баги в стиле «рельсы лежат кривыми палками».

A browser-based TF2 demo replay viewer: load a `.dem`, watch the fight from any
camera angle — free-fly, follow any player, or first-person POV — without launching
the game.

**Браузерный просмотрщик демо TF2**: загрузи `.dem` и смотри бой с любой камеры —
свободный полёт, слежение за игроком, вид от первого лица — без запуска игры.

## What it shows

- `pl_upward` (and other Source BSP v20 maps) rendered from brush geometry,
  displacements and static props — vertex-colored, flat-shaded, real-time shadows.
- Players as team-colored capsules (red/blue) with name, HP, class and view-direction
  arrows; spies are purple with a true-team ring when disguised.
- Rockets, pipes, stickies, flares flying and exploding with splash-radius rings.
- In-game style damage numbers (crits in yellow).
- Health/ammo kit spawn pads marked on the floor.
- Killfeed, chat, time remaining, alive/dead counters.
- **Known limitation**: this is a POV demo — the server only sent the recording player
  what was in their PVS. Blind spots exist and are inherent to the file, not the viewer.

## How it works

```
.dem ──(preprocess: Rust, tf-demo-parser)──► replay.bin + meta.json
map.bsp ──(tools: python, stdlib)──► map.glb
                        └──► web/ (Vite + TypeScript + three.js)
```

All heavy lifting happens once, offline. The browser gets a compact tick-by-tick
replay (~35 MB) and a lightweight GLB map (~4.5 MB).

## Quick start

Requirements: Rust, Python 3, Node.js, and a `pl_upward.bsp` (see `tools/get_map.sh`).

```sh
# 1. Replay data from a demo file
cd preprocess && cargo build --release
./target/release/dem2replay /path/to/demo.dem ../web/public/replay
python3 validate.py ../web/public/replay/replay.bin

# 2. Map geometry
cd ..
python3 tools/bsp2glb.py assets/pl_upward.bsp web/public/maps/pl_upward.glb
python3 tools/add_props_glb.py web/public/maps/pl_upward.glb assets/pl_upward.bsp
python3 tools/add_kits_glb.py web/public/maps/pl_upward.glb
python3 tools/validate_glb.py web/public/maps/pl_upward.glb

# 3. Serve
cd web && npm install && npm run dev   # http://localhost:5173
```

Downloading maps: `tools/get_map.sh <mapname>` pulls from public fastdl mirrors.

## Controls

| Action | Desktop | Touch |
|---|---|---|
| Move | WASD + Q/E | virtual joystick |
| Look | right mouse drag | one-finger drag |
| Zoom | mouse wheel | pinch |
| Play/pause | Space | ▶ button |
| Follow player | click in list / F | tap in list |
| POV | V | POV button |

UI is available in Russian and English (toggle in the panel header).

## Status

MVP, works end-to-end on one demo (`demo_upward1.dem`, pl_upward, ~14 min,
24 players). See `TODO.txt` for the roadmap — cart animation, real player models,
map textures, in-browser WASM parsing are the big items.

## Legal note

No Valve assets are bundled or committed. Maps and models must come from your own
TF2 installation or legal sources. Team Fortress 2 is a trademark of Valve Corporation;
this project is not affiliated with or endorsed by Valve.

## License

MIT

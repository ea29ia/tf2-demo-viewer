# Replay format (replay.bin v3)

- Date: 2026-10-22
- Author: health-fix agent
- Scope: `preprocess/src/main.rs`, `web/src/replay.ts`, `../PLAN.md`
- Tags: #replay #format #health

## State

Format v3 is implemented end-to-end and validated. Changes from v2:

- Player record is **27 bytes** (was 26): `health` is now `u16` instead of
  `u8`, inserted at offset +5; the five f32 fields (x/y/z/yaw/pitch) shift by
  one byte. Heavy spawns at 300 HP which does not fit in a u8 (v2 data showed
  every heavy clamped to 255).
- `VERSION` bumped to 3 in writer, reader and `validate.py` (reader is strict:
  it only accepts the version it ships with, matching what the writer emits).
- The dumper now overrides sendprop-derived health with the victim's remaining
  health from global `player_hurt` events when the event is more recent than
  the last observed prop change, and reports 0 for dead players (the sendprop
  often still reads 1 while dead).

## Root-cause notes (health investigation, 2026-10-22)

- `tf-demo-parser`'s `Class` enum matches TF2 class ids: scout 1, sniper 2,
  soldier 3, demoman 4, medic 5, heavy 6, pyro 7, spy 8, engineer 9. An audit
  that assumed `{2: soldier, 5: heavy, ...}` mislabels classes and produces
  fake "wrong health" reports ("heavy plateau at 150" was actually a medic at
  its correct base 150; verified against killfeed weapons: bazaar_bargain ->
  sniper entity, crusaders_crossbow/ubersaw -> medic entity).
- `m_iHealth` sendprops only arrive while the entity is in the recorder's PVS
  (`entity.props()` yields only props present in the current delta). Players
  outside the PVS keep the last in-PVS value (or 0 right after entity
  creation), which looks like a frozen/wrong health plateau. In-PVS values are
  exact: a throwaway instrumentation run compared raw prop arrivals against
  `player_hurt` event health and they matched tick-for-tick for in-PVS
  players.
- `player_hurt` game events are global and carry the victim's remaining
  health (`PlayerHurtEvent.health`), so they are the authoritative source for
  damage-driven changes anywhere on the map. Healing (medic regen, dispensers)
  still only observable via props while in PVS — residual staleness there is
  inherent to the demo format.
- Cargo fingerprints registry crates by content hash: editing the vendored
  source under `~/.cargo/registry/src` does NOT trigger a rebuild. For
  throwaway instrumentation, copy the crate and use a
  `[patch.crates-io]` path dependency in a scratch project.

## Gotchas

- **Projectile record is 17 bytes**, not 24: entity u16, team u8, kind u8,
  flags u8, then 6 quantized i16 (xyz + velocity, units/4). This mismatch
  (writer once wrote 29-byte float records) broke the whole stream after the
  first projectile and surfaced as "missing files" in the viewer. Never change
  one side without the other; see AGENTS.md rule 2.
- **Hurt record is 15 bytes**: victim u16, attacker u16, damage u16, flags u8,
  xyz i16 x3 (units/4), splash i16 (units/4). `splash != 0` marks explosion
  damage; radius is by weapon_id table (rocket/grenade/sticky 146, flare 110).
- **Frame fill**: the dumper emits one frame per tick, repeating the last state
  for ticks without packets, so the timeline is contiguous (55842 frames for
  55842 ticks). `startTick` in meta is `header.signon` (79 here), and absolute
  server ticks from sign-on packets (e.g. 71096) are excluded — a frame with
  tick > header.ticks is skipped.
- Users are keyed by `UserInfo.user_id`; the same id appears as `attacker`/
  `victim` in kills and `attacker`/`victim` in hurt records. Entity ids (u16)
  are a different namespace (used in player/projectile records).
- `validate.py` must be re-run after any writer change; it checks byte-exact
  consumption (no trailing bytes) and meta/binary agreement.

## Open questions

- v4 will add a `shots` block (hit-scan rays) + headshot bit in hurt flags.
  Design is in `../TODO.txt` ("Sniper aim statistics"). Do it as one commit
  touching writer + reader + PLAN.md, bump VERSION to 4.

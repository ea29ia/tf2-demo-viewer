# Replay format (replay.bin v2)

- Date: 2026-10-21
- Author: lead agent
- Scope: `preprocess/src/main.rs`, `web/src/replay.ts`, `../PLAN.md`
- Tags: #replay #format

## State

Format v2 is implemented end-to-end and validated. `PLAN.md` documents v1; v2
adds two blocks per frame (projectiles, hurts) after the player records. The
writer is `preprocess/src/main.rs`, the only reader is `web/src/replay.ts`.

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

- v3 will add a `shots` block (hit-scan rays) + headshot bit in hurt flags.
  Design is in `../TODO.txt` ("Sniper aim statistics"). Do it as one commit
  touching writer + reader + PLAN.md, bump VERSION to 3.

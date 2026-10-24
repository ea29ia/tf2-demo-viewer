//! `dem2replay <demo.dem> <out_dir>`
//!
//! Parses a TF2 demo with `tf-demo-parser`'s `GameStateAnalyser`, then writes
//! `<out_dir>/replay.bin` and `<out_dir>/meta.json` as documented in PLAN.md
//! (Stage A).

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use tf_demo_parser::demo::data::game_state::{Building, GameState, PlayerState};
use tf_demo_parser::demo::gameevent_gen::ItemPickupEvent;
use tf_demo_parser::demo::parser::gamestateanalyser::{GameStateAnalyser, Team};
use tf_demo_parser::{Demo, DemoParser};

const MAGIC: &[u8; 4] = b"TF2R";
const VERSION: u32 = 4;

/// One projectile snapshot, 24-byte on-disk record:
/// entity u16, team u8, kind u8, flags u8, then xyz + velocity as
/// quantized i16 x3 (Source units / 4, enough range ±128k).
#[derive(Debug, Clone, Copy)]
struct ProjectileRecord {
    entity: u16,
    team: u8,
    kind: u8, // 0 rocket, 1 arrow, 2 sticky, 3 pipe, 4 flare, 5 loose cannon, 7 unknown
    flags: u8, // bit0 critical
    x: f32,
    y: f32,
    z: f32,
    vx: f32,
    vy: f32,
    vz: f32,
}

fn q16(v: f32) -> i16 {
    let q = (v / 4.0).round() as i32;
    q.clamp(-32768, 32767) as i16
}

impl ProjectileRecord {
    fn write(&self, out: &mut Vec<u8>) {
        out.extend_from_slice(&self.entity.to_le_bytes());
        out.push(self.team);
        out.push(self.kind);
        out.push(self.flags);
        out.extend_from_slice(&q16(self.x).to_le_bytes());
        out.extend_from_slice(&q16(self.y).to_le_bytes());
        out.extend_from_slice(&q16(self.z).to_le_bytes());
        out.extend_from_slice(&q16(self.vx).to_le_bytes());
        out.extend_from_slice(&q16(self.vy).to_le_bytes());
        out.extend_from_slice(&q16(self.vz).to_le_bytes());
    }
}

/// One player snapshot, exactly the 27-byte on-disk record.
/// Health is u16: heavy spawns at 300 which does not fit in u8.
#[derive(Debug, Clone, Copy)]
struct Record {
    entity: u16,
    team: u8,
    class: u8,
    flags: u8,
    health: u16,
    x: f32,
    y: f32,
    z: f32,
    yaw: f32,
    pitch: f32,
}

impl Record {
    fn write(&self, out: &mut Vec<u8>) {
        out.extend_from_slice(&self.entity.to_le_bytes());
        out.push(self.team);
        out.push(self.class);
        out.push(self.flags);
        out.extend_from_slice(&self.health.to_le_bytes());
        out.extend_from_slice(&self.x.to_le_bytes());
        out.extend_from_slice(&self.y.to_le_bytes());
        out.extend_from_slice(&self.z.to_le_bytes());
        out.extend_from_slice(&self.yaw.to_le_bytes());
        out.extend_from_slice(&self.pitch.to_le_bytes());
    }
}

#[derive(Debug, Clone)]
struct UserEntry {
    entity: u16,
    name: String,
    user_id: u32,
    steam_id: String,
    team: String,
}

fn team_byte(team: Team) -> u8 {
    match team {
        Team::Red => 2,
        Team::Blue => 3,
        _ => 0,
    }
}

fn team_str(team: Team) -> &'static str {
    match team {
        Team::Red => "red",
        Team::Blue => "blue",
        _ => "other",
    }
}

/// Per-user authoritative health observation from `player_hurt` events.
/// The event carries the victim's remaining health and is broadcast for every
/// player (even ones outside the recorder's PVS), so it is more reliable than
/// the `m_iHealth` sendprop, which only arrives while the entity is in PVS.
type HealthOverrides = BTreeMap<u32, (u32, u16)>;

// Debug hook: DEM2REPLAY_DEBUG_EVENTS=1 collects the distinct GameEvent
// variant names seen in the demo and prints them at the end (useful when a
// demo turns out not to carry an event the format relies on).
thread_local! {
    static DEBUG_EVENT_NAMES: std::cell::RefCell<std::collections::BTreeSet<String>> =
        std::cell::RefCell::new(std::collections::BTreeSet::new());
}

fn debug_events_enabled() -> bool {
    static ENABLED: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ENABLED.get_or_init(|| std::env::var("DEM2REPLAY_DEBUG_EVENTS").is_ok())
}

/// Class base health, matching tf-demo-parser's `Class` enum (which mirrors
/// TF2's TF_CLASS_* ids). Used as the spawn-health baseline on respawn.
fn class_base_health(class: tf_demo_parser::demo::parser::analyser::Class) -> u16 {
    use tf_demo_parser::demo::parser::analyser::Class;
    match class {
        Class::Scout | Class::Sniper | Class::Spy | Class::Engineer => 125,
        Class::Soldier => 200,
        Class::Demoman | Class::Pyro => 175,
        Class::Medic => 150,
        Class::Heavy => 300,
        Class::Other => 0,
    }
}

/// Build the ordered record list for a game state. Only red/blue players are
/// included; the order is stable (ascending entity id).
///
/// Health sourcing, freshest source wins:
/// 1. `player_hurt` events (`overrides`): global, authoritative at damage
///    time, work for players outside the recorder's PVS.
/// 2. `m_iHealth` sendprops: exact while the entity is in PVS, but they stop
///    arriving once it leaves, and on PVS re-entry the demo's per-entity
///    instance baseline can resurface a stale value (observed: the same wrong
///    constant reappearing after every respawn).
/// 3. On respawn (dead -> alive with a known recent death) the health is
///    forced to the class base: spawning always restores full health.
///
/// `prop_health`/`prop_change_tick` track when the sendprop stream last
/// actually changed a value; an override only rules while it is more recent
/// than that. A prop change on the respawn tick itself is ignored (that is
/// the stale instance baseline resurfacing, not real damage); any later prop
/// change or hurt event takes over again.
fn snapshot(
    state: &GameState,
    overrides: &mut HealthOverrides,
    deaths: &BTreeMap<u32, u32>,
    prop_health: &mut BTreeMap<u32, u16>,
    prop_change_tick: &mut BTreeMap<u32, u32>,
    prev_alive: &mut BTreeMap<u32, bool>,
    respawn_tick: &mut BTreeMap<u32, u32>,
    tick: u32,
) -> Vec<Record> {
    let mut players: Vec<_> = state
        .players
        .iter()
        .filter(|p| p.team.is_player())
        .collect();
    players.sort_by_key(|p| u32::from(p.entity));

    players
        .into_iter()
        .filter_map(|p| {
            let user_id = u32::from(p.info.as_ref()?.user_id);
            let now_alive = p.state == PlayerState::Alive;
            let mut health = p.health;

            if now_alive && prev_alive.get(&user_id) == Some(&false) {
                if let Some(&died) = deaths.get(&user_id) {
                    // Respawn queue plus a walk out of spawn takes tens of
                    // seconds; 30 s (~1980 ticks) comfortably covers it.
                    if died < tick && tick - died <= 1980 {
                        let base = class_base_health(p.class);
                        if base > 0 {
                            health = base;
                            respawn_tick.insert(user_id, tick);
                            // The death-time override (health 0) must not
                            // survive the respawn: spawning restores health.
                            overrides.remove(&user_id);
                        }
                    }
                }
            }

            if prop_health.get(&user_id) != Some(&p.health) {
                prop_health.insert(user_id, p.health);
                let respawn = respawn_tick.get(&user_id).copied();
                let is_respawn_tick = matches!(respawn, Some(rt) if tick <= rt);
                if !is_respawn_tick {
                    prop_change_tick.insert(user_id, tick);
                }
            }
            if let Some((override_tick, override_health)) = overrides.get(&user_id) {
                let prop_tick = prop_change_tick.get(&user_id).copied().unwrap_or(0);
                if *override_tick > prop_tick {
                    health = *override_health;
                }
            }
            // While dead the sendprop often still reads 1; the analyser's life
            // state is ground truth, so dead players are always reported 0.
            if !now_alive {
                health = 0;
            }
            prev_alive.insert(user_id, now_alive);

            let mut flags = 0u8;
            if now_alive {
                flags |= 1;
            }
            if p.in_pvs {
                flags |= 2;
            }
            if p.has_condition(tf_demo_parser::demo::data::game_state::PlayerCondition::Disguised) {
                flags |= 4;
            }
            if p.has_condition(tf_demo_parser::demo::data::game_state::PlayerCondition::Stealthed) {
                flags |= 8;
            }
            Some(Record {
                entity: u32::from(p.entity) as u16,
                team: team_byte(p.team),
                class: p.class as u8,
                flags,
                health,
                x: p.position.x,
                y: p.position.y,
                z: p.position.z,
                yaw: p.view_angle,
                pitch: p.pitch_angle,
            })
        })
        .collect()
}

fn push_u32(out: &mut Vec<u8>, value: u32) {
    out.extend_from_slice(&value.to_le_bytes());
}

/// One damage (player_hurt) event, 17-byte on-disk record:
/// victim u16, attacker u16, damage u16, flags u8, x/y/z i16 x3 (units/4).
#[derive(Debug, Clone, Copy)]
struct HurtRecord {
    victim: u16,
    attacker: u16,
    damage: u16,
    flags: u8, // bit0 crit, bit1 mini-crit, bit2 fake-crit(all see)
    x: f32,
    y: f32,
    z: f32,
    splash: f32, // radius hint for hitbox visual
}

impl HurtRecord {
    fn write(&self, out: &mut Vec<u8>) {
        out.extend_from_slice(&self.victim.to_le_bytes());
        out.extend_from_slice(&self.attacker.to_le_bytes());
        out.extend_from_slice(&self.damage.to_le_bytes());
        out.push(self.flags);
        out.extend_from_slice(&q16(self.x).to_le_bytes());
        out.extend_from_slice(&q16(self.y).to_le_bytes());
        out.extend_from_slice(&q16(self.z).to_le_bytes());
        out.extend_from_slice(&q16(self.splash).to_le_bytes());
    }
}

/// One engineer building snapshot, exactly the 20-byte on-disk record:
/// entity u16, team u8, kind u8, flags u8, level u8, health u8 (quantized
/// health/maxhealth * 255), angle u16 (degrees/360 * 65535), xyz i16 x3
/// (Source units / 4).
#[derive(Debug, Clone, Copy)]
struct BuildingRecord {
    entity: u16,
    team: u8,
    kind: u8, // 0 sentry, 1 dispenser, 2 teleporter entrance, 3 teleporter exit
    flags: u8, // bit0 building(in-construction), bit1 sapped, bit2 mini
    level: u8,
    health: u8,
    angle: u16,
    x: f32,
    y: f32,
    z: f32,
}

impl BuildingRecord {
    fn write(&self, out: &mut Vec<u8>) {
        out.extend_from_slice(&self.entity.to_le_bytes());
        out.push(self.team);
        out.push(self.kind);
        out.push(self.flags);
        out.push(self.level);
        out.push(self.health);
        out.extend_from_slice(&self.angle.to_le_bytes());
        out.extend_from_slice(&q16(self.x).to_le_bytes());
        out.extend_from_slice(&q16(self.y).to_le_bytes());
        out.extend_from_slice(&q16(self.z).to_le_bytes());
    }
}

/// Quantize a yaw in degrees to u16 (degrees/360 * 65535, normalized to [0,360)).
fn qangle(deg: f32) -> u16 {
    let normalized = deg.rem_euclid(360.0);
    ((normalized / 360.0) * 65535.0).round().clamp(0.0, 65535.0) as u16
}

/// Quantize a building's health to u8 (health/maxhealth * 255).
fn qhealth(health: u16, max_health: u16) -> u8 {
    if max_health == 0 {
        return 0;
    }
    ((health as f32 / max_health as f32) * 255.0)
        .round()
        .clamp(0.0, 255.0) as u8
}

/// One static health/ammo kit placement (from kits.json). Positions are
/// Source units; the on-disk record quantizes them like everything else.
#[derive(Debug, Clone, Copy)]
struct KitDef {
    kind: u8, // 0 health_small, 1 health_medium, 2 health_full, 3 ammo_small, 4 ammo_medium, 5 ammo_full
    x: f32,
    y: f32,
    z: f32,
}

/// Runtime kit tracking: static placements plus per-kit respawn ticks and
/// warning bookkeeping for unrecognized `item_pickup` strings.
struct KitState {
    kits: Vec<KitDef>,
    /// Tick at which each kit reappears; 0 = present (never picked up).
    respawn_at: Vec<u32>,
    /// Unmatched item strings already warned about (avoid log spam).
    warned: std::collections::BTreeSet<String>,
    /// Every distinct `item_pickup` string seen (end-of-run summary).
    seen_items: std::collections::BTreeSet<String>,
}

impl KitState {
    fn new(kits: Vec<KitDef>) -> Self {
        let respawn_at = vec![0u32; kits.len()];
        KitState {
            kits,
            respawn_at,
            warned: std::collections::BTreeSet::new(),
            seen_items: std::collections::BTreeSet::new(),
        }
    }

    fn empty() -> Self {
        Self::new(Vec::new())
    }

    fn len(&self) -> usize {
        self.kits.len()
    }

    /// flags bit0 = present (picked-up kits reappear at respawn_at).
    fn present(&self, index: usize, tick: u32) -> bool {
        self.respawn_at[index] == 0 || tick >= self.respawn_at[index]
    }
}

/// kits.json class name -> kit kind byte (must match PLAN.md v4).
fn kit_kind_from_class(class: &str) -> Option<u8> {
    match class {
        "item_healthkit_small" => Some(0),
        "item_healthkit_medium" => Some(1),
        "item_healthkit_full" => Some(2),
        "item_ammopack_small" => Some(3),
        "item_ammopack_medium" => Some(4),
        "item_ammopack_full" => Some(5),
        _ => None,
    }
}

/// Load the static kit placements written by tools/add_kits_glb.py. The array
/// order of kits.json defines the kit index emitted in the replay (the viewer
/// joins on that index).
fn load_kits(path: &Path) -> Result<Vec<KitDef>, Box<dyn std::error::Error>> {
    let raw = fs::read_to_string(path)?;
    let value: Value = serde_json::from_str(&raw)?;
    let arr = value
        .as_array()
        .ok_or("kits.json must be a JSON array")?;
    let mut kits = Vec::new();
    for (i, entry) in arr.iter().enumerate() {
        let class = entry.get("class").and_then(|v| v.as_str()).unwrap_or("");
        let coord = |key: &str| entry.get(key).and_then(|v| v.as_f64()).unwrap_or(0.0) as f32;
        match kit_kind_from_class(class) {
            Some(kind) => kits.push(KitDef {
                kind,
                x: coord("x"),
                y: coord("y"),
                z: coord("z"),
            }),
            None => eprintln!(
                "warning: kits.json entry {} has unknown class {:?}, skipped",
                i, class
            ),
        }
    }
    Ok(kits)
}

/// Classification of an `item_pickup` event string.
enum ItemKind {
    /// A kit kind byte (see KitDef).
    Kit(u8),
    /// Recognized but not a BSP kit placement (player-dropped medkits).
    Ignored,
    /// No mapping known; warned once per distinct string.
    Unknown,
}

/// Map an `item_pickup` item string to a kit kind. Strings are matched
/// case-insensitively by keywords because TF2 mixes naming conventions
/// ("TF_AMMO_PACK_MEDIUM", "healthkit_small", ...).
fn classify_item(item: &str) -> ItemKind {
    let s = item.to_ascii_lowercase();
    // Medkits are temporary player-created pickups, not map entities.
    if s.contains("medkit") {
        return ItemKind::Ignored;
    }
    let base = if s.contains("health") {
        0u8
    } else if s.contains("ammo") {
        3u8
    } else {
        return ItemKind::Unknown;
    };
    let size = if s.contains("small") {
        0u8
    } else if s.contains("medium") {
        1
    } else if s.contains("full") || s.contains("large") {
        2
    } else {
        return ItemKind::Unknown;
    };
    ItemKind::Kit(base + size)
}

/// Respawn delay in seconds by kit kind (small/medium/full; ammo same scale).
fn kit_respawn_seconds(kind: u8) -> f32 {
    match kind % 3 {
        0 => 10.0,
        1 => 20.0,
        _ => 30.0,
    }
}

/// Apply an `item_pickup` event: mark the nearest kit of the matching kind as
/// picked up (respawn tick = now + respawn seconds / intervalPerTick).
/// Pickups of kinds with no placement nearby (dropped ammo boxes and the
/// like) are silently ignored.
fn handle_item_pickup(
    state: &GameState,
    pickup: &ItemPickupEvent,
    kits: &mut KitState,
    tick: u32,
    interval_per_tick: f32,
) {
    let item: &str = pickup.item.as_ref();
    kits.seen_items.insert(item.to_string());
    let kind = match classify_item(item) {
        ItemKind::Kit(kind) => kind,
        ItemKind::Ignored => return,
        ItemKind::Unknown => {
            if kits.warned.insert(item.to_string()) {
                eprintln!("warning: unmatched item_pickup string {:?}", item);
            }
            return;
        }
    };
    let picker_pos = state
        .players
        .iter()
        .find(|p| {
            p.info.as_ref().map(|i| u32::from(i.user_id)) == Some(u32::from(pickup.user_id))
        })
        .map(|p| p.position)
        .unwrap_or_default();
    let mut best: Option<(usize, f32)> = None;
    for (i, kit) in kits.kits.iter().enumerate() {
        if kit.kind != kind {
            continue;
        }
        let d2 = (kit.x - picker_pos.x).powi(2)
            + (kit.y - picker_pos.y).powi(2)
            + (kit.z - picker_pos.z).powi(2);
        if best.map_or(true, |(_, best_d2)| d2 < best_d2) {
            best = Some((i, d2));
        }
    }
    // Kit pickup radius: a player must touch the kit; 200 u is generous but
    // kit placements are far apart on pl_upward.
    const MAX_PICKUP_DISTANCE: f32 = 200.0;
    if let Some((index, d2)) = best {
        if d2.sqrt() <= MAX_PICKUP_DISTANCE {
            let ticks = if interval_per_tick > 0.0 {
                (kit_respawn_seconds(kind) / interval_per_tick).round() as u32
            } else {
                0
            };
            kits.respawn_at[index] = tick + ticks;
        }
    }
}

/// Collect `player_hurt` and `item_pickup` events for the current tick:
/// hurts record the victims' authoritative post-hit health into `overrides`
/// and death ticks into `deaths`; pickups mark the matching kit as taken.
/// `cursor` marks how many leading entries of `state.events` were
/// already consumed on earlier ticks: the analyser only ever appends to that
/// vector and never clears it, so scanning the whole vector every tick would
/// be O(n) per tick on long demos. Consumed entries sit at indices
/// `[0..cursor)` and are skipped; entries for future ticks are left for later
/// calls.
///
/// Game events are retransmitted by the server in later packets, so the same
/// event can appear in the vector several times; `seen` (hashes of processed
/// events) keeps bookkeeping, hurt output and kit pickups idempotent. Each
/// event is therefore processed at first sight, even if its recorded tick is
/// older than the current packet tick (late delivery); the current tick is
/// used as the observation time for overrides, deaths and kit respawns. Hurt
/// records are only emitted for events whose tick matches the current tick,
/// as before.
fn event_snapshot(
    state: &GameState,
    cursor: &mut usize,
    overrides: &mut HealthOverrides,
    deaths: &mut BTreeMap<u32, u32>,
    seen: &mut std::collections::HashSet<u64>,
    kits: &mut KitState,
    interval_per_tick: f32,
) -> Vec<HurtRecord> {
    use std::hash::{Hash, Hasher};
    let mut out = Vec::new();
    let tick = u32::from(state.tick);
    // The borrow is valid for this call only; indices are stable within it
    // because the vector is only appended to between calls.
    let events = &state.events[*cursor..];
    let mut consumed = 0usize;
    for (i, (event_tick, event)) in events.iter().enumerate() {
        let event_tick = u32::from(*event_tick);
        if event_tick > tick {
            // Events are pushed in nondecreasing tick order, so the first
            // future-tick event ends the scan.
            break;
        }
        // Deduplicate retransmissions by event content (GameEvent does not
        // implement Hash, so hash its Debug representation).
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        event_tick.hash(&mut hasher);
        format!("{:?}", event).hash(&mut hasher);
        if !seen.insert(hasher.finish()) {
            consumed = i + 1;
            continue;
        }
        if debug_events_enabled() {
            let name = {
                let s = format!("{:?}", event);
                let end = s.find(['(', ' ', '{']).unwrap_or(s.len());
                s[..end].to_string()
            };
            DEBUG_EVENT_NAMES.with(|set| {
                set.borrow_mut().insert(name);
            });
        }
        consumed = i + 1;
        if let tf_demo_parser::demo::gameevent_gen::GameEvent::PlayerDeath(d) = event {
            // Some kills (e.g. certain world/environment deaths) never produce
            // a player_hurt event, so death detection needs this event too.
            deaths.insert(u32::from(d.user_id), tick);
            continue;
        }
        if let tf_demo_parser::demo::gameevent_gen::GameEvent::PlayerHurt(h) = event {
            // The event's `health` field is the victim's remaining health
            // straight from the server: authoritative even for players the
            // recorder cannot see (out-of-PVS sendprops never arrive).
            overrides.insert(u32::from(h.user_id), (tick, h.health));
            if h.health == 0 {
                // Death: remembered so the next dead -> alive transition can
                // be recognised as a respawn (see snapshot).
                deaths.insert(u32::from(h.user_id), tick);
            }
            if event_tick != tick {
                // Late retransmission of an already-emitted tick: record the
                // health above, but do not duplicate the hurt record.
                continue;
            }
            let pos = state
                .players
                .iter()
                .find(|p| p.info.as_ref().map(|i| u32::from(i.user_id)) == Some(u32::from(h.user_id)))
                .map(|p| p.position)
                .unwrap_or_default();
            // Explosion weapons: give the hitbox ring a realistic radius.
            const ROCKET: u16 = 18; // tf_weapon_rocketlauncher
            const GRENADE: u16 = 19; // tf_weapon_grenadelauncher
            const STICKY: u16 = 20; // tf_weapon_pipebomblauncher
            const FLARE: u16 = 12; // flare gun-ish
            const SENTRY_ROCKET: u16 = 43;
            let splash = match h.weapon_id {
                ROCKET | GRENADE | STICKY | SENTRY_ROCKET => 146.0f32,
                FLARE => 110.0,
                _ => 0.0,
            };
            let mut flags = 0u8;
            if h.crit {
                flags |= 1;
            }
            if h.mini_crit {
                flags |= 2;
            }
            if h.all_see_crit {
                flags |= 4;
            }
            out.push(HurtRecord {
                victim: h.user_id,
                attacker: h.attacker,
                damage: h.damage_amount,
                flags,
                x: pos.x,
                y: pos.y,
                z: pos.z,
                splash,
            });
        }
        if let tf_demo_parser::demo::gameevent_gen::GameEvent::ItemPickup(pickup) = event {
            handle_item_pickup(state, pickup, kits, tick, interval_per_tick);
        }
    }
    *cursor += consumed;
    // Defensive cap: the on-disk record count is u16 and a runaway event
    // backlog must never corrupt the stream. Should never trigger after the
    // cursor fix.
    const MAX_HURTS_PER_FRAME: usize = 4096;
    if out.len() > MAX_HURTS_PER_FRAME {
        eprintln!(
            "warning: truncating hurt snapshot at tick {} ({} -> {})",
            tick,
            out.len(),
            MAX_HURTS_PER_FRAME
        );
        out.truncate(MAX_HURTS_PER_FRAME);
    }
    out
}

fn projectile_snapshot(state: &GameState) -> Vec<ProjectileRecord> {
    state
        .projectiles
        .values()
        .filter(|p| p.team.is_player())
        .filter(|p| p.position.x != 0.0 || p.position.y != 0.0 || p.position.z != 0.0)
        .map(|p| ProjectileRecord {
            entity: u32::from(p.id) as u16,
            team: team_byte(p.team),
            kind: p.ty as u8,
            flags: if p.critical { 1 } else { 0 },
            x: p.position.x,
            y: p.position.y,
            z: p.position.z,
            vx: p.initial_speed.x,
            vy: p.initial_speed.y,
            vz: p.initial_speed.z,
        })
        .collect()
}

/// Per-frame cap for the building block (u16 count would allow more, the cap
/// keeps frames bounded like MAX_HURTS_PER_FRAME).
const MAX_BUILDINGS_PER_FRAME: usize = 32;

/// Build the building snapshot for a game state. Buildings whose position is
/// still all-zero have not received their placement sendprops yet (the
/// analyser creates them on entity entry) and are skipped, as are
/// non-red/blue teams (spectator/unassigned leftovers).
fn building_snapshot(state: &GameState, truncations: &mut u64) -> Vec<BuildingRecord> {
    let tick = u32::from(state.tick);
    let mut out: Vec<BuildingRecord> = state
        .buildings
        .values()
        .filter_map(|b| {
            let pos = b.position();
            if pos.x == 0.0 && pos.y == 0.0 && pos.z == 0.0 {
                return None;
            }
            let team = team_byte(b.team());
            if team == 0 {
                return None;
            }
            let (kind, building, sapped, mini, level) = match b {
                Building::Sentry(s) => (0u8, s.building, s.sapped, s.is_mini, s.level),
                Building::Dispenser(d) => (1u8, d.building, d.sapped, false, d.level),
                Building::Teleporter(t) => (
                    if t.is_entrance { 2u8 } else { 3u8 },
                    t.building,
                    t.sapped,
                    false,
                    t.level,
                ),
                // non_exhaustive enum: tolerate future building types.
                _ => return None,
            };
            let mut flags = 0u8;
            if building {
                flags |= 1;
            }
            if sapped {
                flags |= 2;
            }
            if mini {
                flags |= 4;
            }
            Some(BuildingRecord {
                entity: u32::from(b.entity_id()) as u16,
                team,
                kind,
                flags,
                level,
                health: qhealth(b.health(), b.max_health()),
                angle: qangle(b.angle()),
                x: pos.x,
                y: pos.y,
                z: pos.z,
            })
        })
        .collect();
    out.sort_by_key(|r| r.entity);
    if out.len() > MAX_BUILDINGS_PER_FRAME {
        *truncations += 1;
        if *truncations == 1 || *truncations % 1000 == 0 {
            eprintln!(
                "warning: truncating building snapshot at tick {} ({} -> {})",
                tick,
                out.len(),
                MAX_BUILDINGS_PER_FRAME
            );
        }
        out.truncate(MAX_BUILDINGS_PER_FRAME);
    }
    out
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = env::args();
    let _exe = args.next();
    let demo_path = args.next().ok_or("usage: dem2replay <demo.dem> <out_dir> [--kits <kits.json>]")?;
    let out_dir = args.next().ok_or("usage: dem2replay <demo.dem> <out_dir> [--kits <kits.json>]")?;
    let out_dir = PathBuf::from(out_dir);

    // Optional flag: static kit placements (kits.json from add_kits_glb.py).
    // Without it (or on a load failure) the kit block is emitted with count 0.
    let mut kits_path: Option<PathBuf> = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--kits" => {
                let path = args
                    .next()
                    .ok_or("--kits requires a kits.json path")?;
                kits_path = Some(PathBuf::from(path));
            }
            other => return Err(format!("unknown argument {:?}", other).into()),
        }
    }
    let mut kits = match &kits_path {
        Some(path) => match load_kits(path) {
            Ok(loaded) => KitState::new(loaded),
            Err(err) => {
                eprintln!(
                    "warning: failed to load kits from {} ({}); kit records disabled",
                    path.display(),
                    err
                );
                KitState::empty()
            }
        },
        None => {
            eprintln!("note: no --kits path given; kit records disabled");
            KitState::empty()
        }
    };

    let file = fs::read(&demo_path)?;
    let demo = Demo::new(&file);
    let parser = DemoParser::new_with_analyser(demo.get_stream(), GameStateAnalyser::new());
    let (header, mut ticker) = parser.ticker()?;

    // Frame bodies are appended first; the final frame_count is prepended later.
    let mut frames: Vec<u8> = Vec::new();
    let mut frame_count: u32 = 0;
    let mut max_players: usize = 0;
    let mut max_projectiles: usize = 0;
    let mut max_buildings: usize = 0;
    let mut building_truncations: u64 = 0;
    let mut min_tick: Option<u32> = None;
    let mut max_tick: Option<u32> = None;
    let mut bbox = [f32::INFINITY, f32::INFINITY, f32::INFINITY,
                    f32::NEG_INFINITY, f32::NEG_INFINITY, f32::NEG_INFINITY];

    // Last seen map/team data, accumulated across the whole demo.
    let mut users: BTreeMap<u32, UserEntry> = BTreeMap::new();
    let mut interval_per_tick = 0f32;
    let mut last_tick: Option<u32> = None;
    // Frame emission starts only after the signon phase: the first packets
    // (signon) carry absolute server ticks that would otherwise start the
    // timeline far above 0, and the SyncTick packet then resets the tick to 0,
    // producing a backwards timeline. `seen_sync` flips on the first tick-0
    // packet (the SyncTick boundary).
    let mut seen_sync = false;
    // Cursor into the analyser's event vector, see event_snapshot().
    let mut hurt_cursor: usize = 0;
    // Authoritative health from player_hurt events, per user id (see snapshot).
    let mut health_overrides: HealthOverrides = BTreeMap::new();
    // Last tick a user was known dead with 0 health (see snapshot).
    let mut death_ticks: BTreeMap<u32, u32> = BTreeMap::new();
    // Last sendprop-derived health per user id, and the tick it last changed.
    let mut prop_health: BTreeMap<u32, u16> = BTreeMap::new();
    let mut prop_change_tick: BTreeMap<u32, u32> = BTreeMap::new();
    // Per-user alive flag from the previous emitted tick, and the tick of the
    // last forced respawn reset.
    let mut prev_alive: BTreeMap<u32, bool> = BTreeMap::new();
    let mut respawn_tick: BTreeMap<u32, u32> = BTreeMap::new();
    // Hashes of already-processed game events (retransmission dedupe).
    let mut seen_events: std::collections::HashSet<u64> = std::collections::HashSet::new();

    while let Some(t) = ticker.next()? {
        let state = t.state;
        if state.interval_per_tick > 0.0 {
            interval_per_tick = state.interval_per_tick;
        }

        for p in state.players.iter().filter(|p| p.team.is_player()) {
            let Some(info) = p.info.as_ref() else { continue };
            let user_id = u32::from(info.user_id);
            users.insert(
                user_id,
                UserEntry {
                    entity: u32::from(p.entity) as u16,
                    name: info.name.clone(),
                    user_id,
                    steam_id: info.steam_id.clone(),
                    team: team_str(p.team).to_string(),
                },
            );
        }

        let tick = u32::from(state.tick);
        // Sign-on packets carry absolute server ticks; only the demo tick range
        // is part of the replay timeline.
        if tick > header.ticks {
            continue;
        }
        if tick == 0 {
            // SyncTick boundary: the real timeline starts here.
            seen_sync = true;
        }
        if !seen_sync {
            // Still in the signon phase: update users/state but emit no
            // frames. Consume all buffered events so the hurt cursor never
            // has to rescan them (their ticks are outside the timeline).
            hurt_cursor = state.events.len();
            continue;
        }
        if last_tick == Some(tick) {
            continue;
        }

        // Emit every intermediate tick as well. Demos occasionally skip a tick
        // without a packet; filling those keeps the timeline continuous and
        // gives the web player a dense frame series.
        let start = match last_tick {
            Some(prev) if tick > prev => prev + 1,
            Some(prev) if tick < prev => {
                // Timeline went backwards (must not happen once the signon
                // phase is suppressed). Restart from the current tick rather
                // than filling backwards.
                eprintln!("warning: tick went backwards {} -> {}", prev, tick);
                tick
            }
            _ => tick,
        };

        // Hurt and pickup events must be consumed before the snapshot so this
        // tick's health corrections and kit respawns are already in place
        // when records are built.
        let hurts = event_snapshot(
            state,
            &mut hurt_cursor,
            &mut health_overrides,
            &mut death_ticks,
            &mut seen_events,
            &mut kits,
            interval_per_tick,
        );
        let records = snapshot(
            state,
            &mut health_overrides,
            &death_ticks,
            &mut prop_health,
            &mut prop_change_tick,
            &mut prev_alive,
            &mut respawn_tick,
            tick,
        );
        let projectiles = projectile_snapshot(state);
        let buildings = building_snapshot(state, &mut building_truncations);
        let mut max_proj = 0usize;
        for filled in start..=tick {
            push_u32(&mut frames, filled);
            frames.extend_from_slice(&(records.len() as u16).to_le_bytes());
            for r in &records {
                r.write(&mut frames);
                for (i, value) in [r.x, r.y, r.z].into_iter().enumerate() {
                    if i == 0 {
                        bbox[0] = bbox[0].min(value);
                        bbox[3] = bbox[3].max(value);
                    } else if i == 1 {
                        bbox[1] = bbox[1].min(value);
                        bbox[4] = bbox[4].max(value);
                    } else {
                        bbox[2] = bbox[2].min(value);
                        bbox[5] = bbox[5].max(value);
                    }
                }
            }
            frames.extend_from_slice(&(projectiles.len() as u16).to_le_bytes());
            for p in &projectiles {
                p.write(&mut frames);
            }
            frames.extend_from_slice(&(hurts.len() as u16).to_le_bytes());
            for h in &hurts {
                h.write(&mut frames);
            }
            // v4: buildings block, then kits block. The kit present flag is
            // evaluated per filled tick so intermediate frames show the
            // pre-pickup state.
            frames.extend_from_slice(&(buildings.len() as u16).to_le_bytes());
            for b in &buildings {
                b.write(&mut frames);
            }
            frames.extend_from_slice(&(kits.len() as u16).to_le_bytes());
            for (index, kit) in kits.kits.iter().enumerate() {
                frames.extend_from_slice(&(index as u16).to_le_bytes());
                frames.push(kit.kind);
                frames.push(if kits.present(index, filled) { 1 } else { 0 });
                frames.extend_from_slice(&q16(kit.x).to_le_bytes());
                frames.extend_from_slice(&q16(kit.y).to_le_bytes());
                frames.extend_from_slice(&q16(kit.z).to_le_bytes());
            }
            max_proj = max_proj.max(projectiles.len());
            frame_count += 1;
            min_tick = Some(min_tick.map_or(filled, |m| m.min(filled)));
            max_tick = Some(max_tick.map_or(filled, |m| m.max(filled)));
        }
        max_players = max_players.max(records.len());
        max_projectiles = max_projectiles.max(max_proj);
        max_buildings = max_buildings.max(buildings.len());
        last_tick = Some(tick);
    }

    let user_values: Vec<Value> = users
        .values()
        .map(|u| {
            json!({
                "entity": u.entity,
                "name": &u.name,
                "userId": u.user_id,
                "steamId": &u.steam_id,
                "team": &u.team,
            })
        })
        .collect();

    let kill_values: Vec<Value> = ticker
        .state()
        .kills
        .iter()
        .map(|k| {
            json!({
                "tick": k.tick,
                "attacker": k.attacker_id,
                "victim": k.victim_id,
                "assister": k.assister_id,
                "weapon": &k.weapon,
            })
        })
        .collect();

    let interval = if interval_per_tick > 0.0 {
        interval_per_tick
    } else {
        header.duration / header.ticks.max(1) as f32
    };
    let tick_rate = if interval > 0.0 { 1.0 / interval } else { 0.0 };

    let meta = json!({
        "map": &header.map,
        "nick": &header.nick,
        "duration": header.duration,
        "ticks": header.ticks,
        "frames": frame_count,
        "intervalPerTick": interval,
        "tickRate": tick_rate,
        "startTick": header.signon,
        "users": user_values,
        "kills": kill_values,
    });
    let meta_json = serde_json::to_string(&meta)?;

    fs::create_dir_all(&out_dir)?;
    write_meta(&out_dir, &meta_json)?;

    let mut bin = Vec::with_capacity(16 + meta_json.len() + frames.len());
    bin.extend_from_slice(MAGIC);
    push_u32(&mut bin, VERSION);
    push_u32(&mut bin, meta_json.len() as u32);
    bin.extend_from_slice(meta_json.as_bytes());
    push_u32(&mut bin, frame_count);
    bin.extend_from_slice(&frames);
    fs::write(out_dir.join("replay.bin"), &bin)?;

    let bbox = if min_tick.is_some() {
        bbox
    } else {
        [0.0; 6]
    };

    println!("demo:      {}", Path::new(&demo_path).display());
    println!("map:       {}", header.map);
    println!("nick:      {}", header.nick);
    println!("duration:  {:.2}s", header.duration);
    println!("ticks:     {}", header.ticks);
    println!("frames:    {}", frame_count);
    println!("max players: {}", max_players);
    println!("max projectiles: {}", max_projectiles);
    println!("max buildings: {}", max_buildings);
    if building_truncations > 0 {
        println!(
            "building snapshot truncated on {} frames (cap {})",
            building_truncations, MAX_BUILDINGS_PER_FRAME
        );
    }
    println!("kits:      {}", kits.len());
    if !kits.seen_items.is_empty() {
        println!("item_pickup strings seen: {:?}", kits.seen_items);
    } else {
        eprintln!("note: no item_pickup events were seen in this demo");
    }
    if debug_events_enabled() {
        DEBUG_EVENT_NAMES.with(|set| println!("debug event variants: {:?}", set.borrow()));
    }
    println!(
        "tick range: {}..={}",
        min_tick.unwrap_or(0),
        max_tick.unwrap_or(0)
    );
    println!(
        "bbox:      x[{:.1},{:.1}] y[{:.1},{:.1}] z[{:.1},{:.1}]",
        bbox[0], bbox[3], bbox[1], bbox[4], bbox[2], bbox[5]
    );
    println!("users:     {}", users.len());
    println!("kills:     {}", kill_values.len());
    println!("out:       {}", out_dir.display());
    Ok(())
}

fn write_meta(out_dir: &Path, meta_json: &str) -> std::io::Result<()> {
    fs::write(out_dir.join("meta.json"), meta_json)
}

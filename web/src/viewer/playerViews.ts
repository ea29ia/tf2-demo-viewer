// Per-player view objects (capsule fallback, real model holder, arrow, ring,
// name label) — extracted verbatim from main.ts.

import * as THREE from 'three';
import { TEAM_RED, CLASS_SPY, teamColor, CAPSULE_RADIUS, CAPSULE_LENGTH, CAPSULE_CENTER_Y } from './tf2';
import { state } from './state';
import { createNameLabel, drawNameLabel, type NameLabel } from './nameLabels';
import { getModelTemplate, instantiateModel, type PlayerModelInstance } from '../playerModels';
import { FLAG_ALIVE, FLAG_IN_PVS, FLAG_DISGUISED, FLAG_STEALTHED, type Pose } from '../replay';
import { playerGroup } from './scene';

export interface PlayerView {
  entity: number;
  name: string;
  team: number;
  cls: number;
  health: number;
  flags: number;
  alive: boolean;
  inPvs: boolean;
  seen: boolean;
  group: THREE.Group;
  capsule: THREE.Mesh<THREE.CapsuleGeometry, THREE.MeshStandardMaterial>;
  modelHolder: THREE.Group;
  model: PlayerModelInstance | null;
  modelKey: string;
  arrow: THREE.Mesh<THREE.ConeGeometry, THREE.MeshBasicMaterial>;
  ring: THREE.Mesh<THREE.TorusGeometry, THREE.MeshBasicMaterial>;
  label: NameLabel;
}

export const views = new Map<number, PlayerView>();

export function createPlayerView(entity: number, name: string, team: number): PlayerView {
  const group = new THREE.Group();
  group.visible = false;

  const material = new THREE.MeshStandardMaterial({
    color: teamColor(team),
    emissive: teamColor(team),
    emissiveIntensity: 0.5,
    roughness: 0.62,
    metalness: 0.05,
    transparent: true,
    opacity: 1,
  });

  const geometry = new THREE.CapsuleGeometry(CAPSULE_RADIUS, CAPSULE_LENGTH, 8, 18);
  const capsule = new THREE.Mesh(geometry, material);
  capsule.position.y = CAPSULE_CENTER_Y;
  capsule.castShadow = true;
  group.add(capsule);

  // Real player model holder (models swap on (cls, team) change).
  const modelHolder = new THREE.Group();
  modelHolder.visible = false;
  group.add(modelHolder);

  // View direction arrow: a thin cone in front of the capsule.
  const arrow = new THREE.Mesh(
    new THREE.ConeGeometry(9, 34, 10),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85 }),
  );
  arrow.rotation.x = Math.PI / 2; // cone points +Z in group space
  arrow.position.set(0, CAPSULE_CENTER_Y, CAPSULE_RADIUS + 17);
  group.add(arrow);

  // True-team ring at the feet (visible only for disguised spies).
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(CAPSULE_RADIUS + 6, 2.5, 6, 24),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.95 }),
  );
  ring.rotation.x = Math.PI / 2;
  ring.position.y = 4;
  ring.visible = false;
  group.add(ring);

  const label = createNameLabel();
  label.sprite.position.y = 112;
  group.add(label.sprite);

  playerGroup.add(group);

  const view: PlayerView = {
    entity,
    name,
    team,
    cls: 0,
    health: 0,
    flags: 0,
    alive: true,
    inPvs: true,
    seen: false,
    group,
    capsule,
    modelHolder,
    model: null,
    modelKey: '',
    arrow,
    ring,
    label,
  };
  views.set(entity, view);
  return view;
}

export function viewFor(entity: number): PlayerView {
  const existing = views.get(entity);
  if (existing) return existing;
  const name = state.metaUsers.get(entity) ?? `#${entity}`;
  return createPlayerView(entity, name, TEAM_RED);
}

export function opacityOf(view: PlayerView): number {
  let opacity = 1;
  if (!view.alive) opacity = 0.24;
  if ((view.flags & FLAG_STEALTHED) !== 0 && view.alive) opacity = Math.min(opacity, 0.45);
  if (view.inPvs === false && state.dimPvs) opacity = Math.min(opacity, 0.35);
  return opacity;
}

/** Per-instance material styling for real models (cloned materials). */
export function applyModelStyle(
  view: PlayerView,
  opacity: number,
  isSpy: boolean,
  pose: Pose,
): void {
  if (!view.model) return;
  const disguised = isSpy && (pose.flags & FLAG_DISGUISED) !== 0;
  const stealthed = isSpy && (pose.flags & FLAG_STEALTHED) !== 0;
  for (const mat of view.model.materials) {
    if (disguised) {
      // Disguised spy: purple emissive tint + the true-team ring below.
      mat.emissive.setHex(0x9b59d0);
      mat.emissiveIntensity = 0.35;
    } else if (stealthed) {
      mat.emissive.setHex(0x8a7fae);
      mat.emissiveIntensity = 0.2;
    } else {
      mat.emissive.setHex(0x000000);
      mat.emissiveIntensity = view.alive ? 0 : 0.08;
    }
    mat.opacity = opacity;
    mat.depthWrite = opacity > 0.7;
  }
}

export function applyPose(view: PlayerView, pose: Pose): void {
  view.team = pose.team;
  view.cls = pose.cls;
  view.flags = pose.flags;
  view.health = pose.health;
  view.alive = (pose.flags & FLAG_ALIVE) !== 0;
  view.inPvs = (pose.flags & FLAG_IN_PVS) !== 0;
  view.seen = true;

  // Source Z-up -> three.js Y-up: (x, y, z) -> (x, z, -y)
  view.group.position.set(pose.x, pose.z, -pose.y);
  view.group.rotation.y = THREE.MathUtils.degToRad(pose.yaw);

  const mat = view.capsule.material;
  const isSpy = pose.cls === CLASS_SPY;
  const stealthed = pose.stealthed;

  // --- Real model handling -------------------------------------------------
  const key = `${pose.cls}_${pose.team}`;
  if (key !== view.modelKey) {
    view.modelKey = key;
    void getModelTemplate(pose.cls, pose.team).then((template) => {
      // Ignore stale results: the player may have changed class meanwhile.
      if (template === null || `${pose.cls}_${pose.team}` !== view.modelKey) return;
      if (view.model) {
        view.modelHolder.remove(view.model.group);
        view.model = null;
      }
      view.model = instantiateModel(template);
      view.modelHolder.add(view.model.group);
      applyModelStyle(view, opacityOf(view), isSpy, pose);
    });
  }
  const hasModel = view.model !== null && state.showModels;
  view.modelHolder.visible = hasModel;
  view.capsule.visible = !hasModel;
  view.arrow.visible = !hasModel;
  if (view.model && hasModel) applyModelStyle(view, opacityOf(view), isSpy, pose);

  if (!hasModel) {
    if (isSpy && pose.disguised) {
      // Disguised spy: purple body with a ring of the *real* team color.
      mat.color.setHex(0x9b59d0);
      mat.emissive.setHex(0x9b59d0);
    } else if (isSpy && stealthed) {
      // Cloaked spy: faint translucent gray-violet.
      mat.color.setHex(0x8a7fae);
      mat.emissive.setHex(0x8a7fae);
    } else {
      mat.color.setHex(teamColor(pose.team));
      mat.emissive.setHex(teamColor(pose.team));
    }
    mat.emissiveIntensity = view.alive ? 0.5 : 0.08;
  }

  const opacity = opacityOf(view);
  mat.opacity = hasModel ? 1 : opacity;
  mat.depthWrite = hasModel || opacity > 0.7;

  // Real-team ring: a thin torus at the feet shows the true team for disguised spies.
  if (isSpy && pose.disguised) {
    view.ring.visible = view.alive;
    (view.ring.material as THREE.MeshBasicMaterial).color.setHex(teamColor(view.team));
  } else {
    view.ring.visible = false;
  }

  if (view.alive) {
    view.capsule.rotation.z = 0;
    view.capsule.position.y = CAPSULE_CENTER_Y;
  } else {
    view.capsule.rotation.z = Math.PI / 2;
    view.capsule.position.y = CAPSULE_RADIUS;
  }

  // Dead players keep the upright model but fade (opacity handled above);
  // a capsule fallback lies on its side as before.

  view.group.visible = !(state.hideDead && !view.alive);
  view.label.sprite.visible = view.alive && opacity > 0.4;
  (view.label.sprite.material as THREE.SpriteMaterial).opacity = opacity > 0.5 ? 1 : 0.55;

  drawNameLabel(view.label, view);
}

export function hideAllViews(): void {
  for (const view of views.values()) view.group.visible = false;
}

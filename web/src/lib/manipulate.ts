import * as THREE from "three";
import type { DesignSpec } from "./api";

export type HandleType = "north" | "south" | "east" | "west" | "top";

const HANDLE_COLOUR = 0xf59e0b;

const DIRECTION: Record<HandleType, THREE.Vector3> = {
  east: new THREE.Vector3(1, 0, 0),
  west: new THREE.Vector3(-1, 0, 0),
  north: new THREE.Vector3(0, 1, 0),
  south: new THREE.Vector3(0, -1, 0),
  top: new THREE.Vector3(0, 0, 1),
};

export function createGizmos(width: number, depth: number, height: number): {
  group: THREE.Group;
  handles: THREE.Mesh[];
  ghost: THREE.LineSegments;
  updateGhost: (w: number, d: number, h: number) => void;
  dispose: () => void;
} {
  const group = new THREE.Group();
  const reach = Math.max(0.5, Math.min(width, depth) * 0.11);

  const ghostGeometry = new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1));
  const ghostMaterial = new THREE.LineBasicMaterial({
    color: HANDLE_COLOUR,
    transparent: true,
    opacity: 0.9,
    depthTest: false,
    toneMapped: false,
  });
  const ghost = new THREE.LineSegments(ghostGeometry, ghostMaterial);
  ghost.position.set(0, 0, height / 2);
  ghost.scale.set(width, depth, height);
  ghost.visible = false;
  ghost.renderOrder = 999;
  group.add(ghost);

  const coneGeometry = new THREE.ConeGeometry(reach * 0.34, reach * 0.8, 16);
  coneGeometry.translate(0, reach * 0.4, 0);
  const stemGeometry = new THREE.CylinderGeometry(reach * 0.055, reach * 0.055, reach, 8);
  stemGeometry.translate(0, reach * 0.5, 0);

  const handles: THREE.Mesh[] = [];
  const shells: THREE.Group[] = [];

  const place = (shell: THREE.Group, type: HandleType, w: number, d: number, h: number) => {
    const anchor = type === "east" ? new THREE.Vector3(w / 2, 0, h / 2)
      : type === "west" ? new THREE.Vector3(-w / 2, 0, h / 2)
      : type === "north" ? new THREE.Vector3(0, d / 2, h / 2)
      : type === "south" ? new THREE.Vector3(0, -d / 2, h / 2)
      : new THREE.Vector3(0, 0, h);
    shell.position.copy(anchor);
  };

  const createHandle = (type: HandleType) => {
    const shell = new THREE.Group();
    shell.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), DIRECTION[type]);
    shell.userData.type = type;

    const material = new THREE.MeshBasicMaterial({
      color: HANDLE_COLOUR,
      depthTest: false,
      transparent: true,
      opacity: 0.92,
      toneMapped: false,
    });
    const stem = new THREE.Mesh(stemGeometry, material);
    stem.renderOrder = 999;
    stem.raycast = () => undefined;
    shell.add(stem);

    const head = new THREE.Mesh(coneGeometry, material);
    head.position.set(0, reach, 0);
    head.name = `gizmo_${type}`;
    head.renderOrder = 999;
    head.userData = { type, active: false };
    shell.add(head);

    group.add(shell);
    shells.push(shell);
    handles.push(head);
    place(shell, type, width, depth, height);
  };

  (["east", "west", "north", "south", "top"] as HandleType[]).forEach(createHandle);

  const updateGhost = (w: number, d: number, h: number) => {
    ghost.scale.set(w, d, h);
    ghost.position.set(0, 0, h / 2);
    for (const shell of shells) place(shell, shell.userData.type as HandleType, w, d, h);
  };

  const dispose = () => {
    ghostGeometry.dispose();
    ghostMaterial.dispose();
    coneGeometry.dispose();
    stemGeometry.dispose();
    for (const handle of handles) {
      const material = handle.material;
      if (Array.isArray(material)) material.forEach((item) => item.dispose());
      else material.dispose();
    }
  };

  return { group, handles, ghost, updateGhost, dispose };
}

export function computeNewDesign(
  base: DesignSpec,
  type: HandleType,
  delta: number,
): DesignSpec {
  const width = Math.sqrt(base.plan.floor_area_m2 * base.plan.aspect_ratio);
  const depth = Math.sqrt(base.plan.floor_area_m2 / base.plan.aspect_ratio);

  let nextWidth = width;
  let nextDepth = depth;
  let nextHeight = base.plan.ceiling_height_m;

  if (type === "east") nextWidth = Math.max(2.0, width + delta * 2);
  else if (type === "west") nextWidth = Math.max(2.0, width - delta * 2);
  else if (type === "north") nextDepth = Math.max(2.0, depth + delta * 2);
  else if (type === "south") nextDepth = Math.max(2.0, depth - delta * 2);
  else if (type === "top") nextHeight = Math.max(1.8, base.plan.ceiling_height_m + delta);

  return {
    ...base,
    plan: {
      ...base.plan,
      floor_area_m2: nextWidth * nextDepth,
      aspect_ratio: nextWidth / nextDepth,
      ceiling_height_m: nextHeight,
    },
  };
}

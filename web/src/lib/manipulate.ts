import * as THREE from "three";
import type { DesignSpec } from "./api";

export type HandleType = "north" | "south" | "east" | "west" | "top";

export function createGizmos(width: number, depth: number, height: number): {
  group: THREE.Group;
  handles: THREE.Mesh[];
  ghost: THREE.LineSegments;
  updateGhost: (w: number, d: number, h: number) => void;
  dispose: () => void;
} {
  const group = new THREE.Group();
  
  // Ghost box (wireframe)
  const ghostGeo = new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1));
  const ghostMat = new THREE.LineBasicMaterial({ color: 0xffa500, transparent: true, opacity: 0.8, depthTest: false });
  const ghost = new THREE.LineSegments(ghostGeo, ghostMat);
  ghost.position.set(0, 0, height / 2);
  ghost.scale.set(width, depth, height);
  ghost.visible = false;
  ghost.renderOrder = 999;
  group.add(ghost);

  // Handles
  const handleGeo = new THREE.BoxGeometry(0.6, 0.6, 0.6);
  const handleMat = new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false, transparent: true, opacity: 0.6 });
  
  const handles: THREE.Mesh[] = [];

  const createHandle = (type: HandleType, pos: [number, number, number]) => {
    const mesh = new THREE.Mesh(handleGeo, handleMat.clone());
    mesh.position.set(...pos);
    mesh.name = `gizmo_${type}`;
    mesh.renderOrder = 999;
    mesh.userData = { type, active: false };
    group.add(mesh);
    handles.push(mesh);
  };

  createHandle("east", [width / 2, 0, height / 2]);
  createHandle("west", [-width / 2, 0, height / 2]);
  createHandle("north", [0, depth / 2, height / 2]);
  createHandle("south", [0, -depth / 2, height / 2]);
  createHandle("top", [0, 0, height]);

  const updateGhost = (w: number, d: number, h: number) => {
    ghost.scale.set(w, d, h);
    ghost.position.set(0, 0, h / 2);
    handles.find(h => h.userData.type === "east")!.position.set(w / 2, 0, h / 2);
    handles.find(h => h.userData.type === "west")!.position.set(-w / 2, 0, h / 2);
    handles.find(h => h.userData.type === "north")!.position.set(0, d / 2, h / 2);
    handles.find(h => h.userData.type === "south")!.position.set(0, -d / 2, h / 2);
    handles.find(h => h.userData.type === "top")!.position.set(0, 0, h);
  };

  const dispose = () => {
    ghostGeo.dispose();
    ghostMat.dispose();
    handleGeo.dispose();
    handleMat.dispose();
    handles.forEach(h => {
      if (h.material) {
        if (Array.isArray(h.material)) h.material.forEach(m => m.dispose());
        else h.material.dispose();
      }
    });
  };

  return { group, handles, ghost, updateGhost, dispose };
}

export function computeNewDesign(
  base: DesignSpec,
  type: HandleType,
  delta: number // signed delta
): DesignSpec {
  const currentArea = base.plan.floor_area_m2;
  const currentAspect = base.plan.aspect_ratio;
  
  const currentWidth = Math.sqrt(currentArea * currentAspect);
  const currentDepth = Math.sqrt(currentArea / currentAspect);
  const currentHeight = base.plan.ceiling_height_m;

  let newW = currentWidth;
  let newD = currentDepth;
  let newH = currentHeight;

  if (type === "east") {
    newW = Math.max(2.0, currentWidth + delta * 2);
  } else if (type === "west") {
    newW = Math.max(2.0, currentWidth - delta * 2);
  } else if (type === "north") {
    newD = Math.max(2.0, currentDepth + delta * 2);
  } else if (type === "south") {
    newD = Math.max(2.0, currentDepth - delta * 2);
  } else if (type === "top") {
    newH = Math.max(1.8, currentHeight + delta);
  }

  const newArea = newW * newD;
  const newAspect = newW / newD;

  return {
    ...base,
    plan: {
      ...base.plan,
      floor_area_m2: newArea,
      aspect_ratio: newAspect,
      ceiling_height_m: newH,
    }
  };
}

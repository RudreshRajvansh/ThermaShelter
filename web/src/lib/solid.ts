import * as THREE from "three";
import type { DesignSpec, GeometryPayload, MeshSurface } from "./api";

const EPSILON = 1e-6;

type Frame = {
  origin: THREE.Vector3;
  u: THREE.Vector3;
  v: THREE.Vector3;
  normal: THREE.Vector3;
};

type Rect = { u0: number; u1: number; v0: number; v1: number };

function corners(surface: MeshSurface): THREE.Vector3[] {
  const points: THREE.Vector3[] = [];
  for (let i = 0; i < surface.positions.length; i += 3) {
    points.push(
      new THREE.Vector3(
        surface.positions[i],
        surface.positions[i + 1],
        surface.positions[i + 2],
      ),
    );
  }
  return points;
}

function frameFor(points: THREE.Vector3[], normal: THREE.Vector3): Frame | null {
  if (points.length < 3) return null;
  const u = new THREE.Vector3().subVectors(points[1], points[0]);
  if (u.lengthSq() < EPSILON) return null;
  u.normalize();
  const v = new THREE.Vector3().crossVectors(normal, u);
  if (v.lengthSq() < EPSILON) return null;
  v.normalize();
  return { origin: points[0].clone(), u, v, normal: normal.clone().normalize() };
}

function toLocal(point: THREE.Vector3, frame: Frame): { u: number; v: number } {
  const delta = new THREE.Vector3().subVectors(point, frame.origin);
  return { u: delta.dot(frame.u), v: delta.dot(frame.v) };
}

function boundsOf(points: THREE.Vector3[], frame: Frame): Rect {
  let u0 = Infinity;
  let u1 = -Infinity;
  let v0 = Infinity;
  let v1 = -Infinity;
  for (const point of points) {
    const local = toLocal(point, frame);
    u0 = Math.min(u0, local.u);
    u1 = Math.max(u1, local.u);
    v0 = Math.min(v0, local.v);
    v1 = Math.max(v1, local.v);
  }
  return { u0, u1, v0, v1 };
}

function partition(outer: Rect, holes: Rect[]): Rect[] {
  const us = new Set<number>([outer.u0, outer.u1]);
  const vs = new Set<number>([outer.v0, outer.v1]);
  for (const hole of holes) {
    us.add(Math.max(outer.u0, hole.u0));
    us.add(Math.min(outer.u1, hole.u1));
    vs.add(Math.max(outer.v0, hole.v0));
    vs.add(Math.min(outer.v1, hole.v1));
  }
  const uEdges = [...us].sort((a, b) => a - b);
  const vEdges = [...vs].sort((a, b) => a - b);

  const cells: Rect[] = [];
  for (let i = 0; i < uEdges.length - 1; i++) {
    for (let j = 0; j < vEdges.length - 1; j++) {
      const cell = {
        u0: uEdges[i],
        u1: uEdges[i + 1],
        v0: vEdges[j],
        v1: vEdges[j + 1],
      };
      if (cell.u1 - cell.u0 < 1e-4 || cell.v1 - cell.v0 < 1e-4) continue;
      const cu = (cell.u0 + cell.u1) / 2;
      const cv = (cell.v0 + cell.v1) / 2;
      const inHole = holes.some(
        (hole) => cu > hole.u0 && cu < hole.u1 && cv > hole.v0 && cv < hole.v1,
      );
      if (!inHole) cells.push(cell);
    }
  }
  return cells;
}

function placeBox(
  rect: Rect,
  frame: Frame,
  thickness: number,
  inset: number,
  material: THREE.Material,
): THREE.Mesh {
  const width = rect.u1 - rect.u0;
  const height = rect.v1 - rect.v0;
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(width, height, thickness),
    material,
  );
  const centre = frame.origin
    .clone()
    .addScaledVector(frame.u, (rect.u0 + rect.u1) / 2)
    .addScaledVector(frame.v, (rect.v0 + rect.v1) / 2)
    .addScaledVector(frame.normal, -(thickness / 2) - inset);
  mesh.position.copy(centre);
  mesh.setRotationFromMatrix(
    new THREE.Matrix4().makeBasis(frame.u, frame.v, frame.normal),
  );
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function constructionThickness(
  design: DesignSpec | null | undefined,
  kind: string,
): number {
  const sum = (layers: { thickness_m: number }[] | undefined) =>
    (layers ?? []).reduce((total, layer) => total + layer.thickness_m, 0);
  if (!design) return kind === "roof" ? 0.2 : 0.3;
  if (kind === "wall") return sum(design.wall?.layers) || 0.3;
  if (kind === "roof") return sum(design.roof?.layers) || 0.2;
  if (kind === "floor") return sum(design.floor?.layers) || 0.25;
  if (kind === "partition") return sum(design.partition?.layers) || 0.1;
  return 0.2;
}

export function buildSolid(
  geometry: GeometryPayload,
  design: DesignSpec | null | undefined,
  materialFor: (surface: MeshSurface) => THREE.Material,
  glassFor: (surface: MeshSurface) => THREE.Material,
): { group: THREE.Group; parts: Map<string, THREE.Mesh[]> } | null {
  const group = new THREE.Group();
  const parts = new Map<string, THREE.Mesh[]>();

  const windowsByParent = new Map<string, MeshSurface[]>();
  for (const surface of geometry.surfaces) {
    if (surface.kind !== "window" || !surface.parent) continue;
    const list = windowsByParent.get(surface.parent) ?? [];
    list.push(surface);
    windowsByParent.set(surface.parent, list);
  }

  let built = 0;
  for (const surface of geometry.surfaces) {
    if (surface.kind === "window" || surface.kind === "shading") continue;

    const points = corners(surface);
    const normal = new THREE.Vector3(...surface.normal);
    const frame = frameFor(points, normal);
    if (!frame) continue;

    const outer = boundsOf(points, frame);
    const holes = (windowsByParent.get(surface.name) ?? []).map((window) =>
      boundsOf(corners(window), frame),
    );
    const thickness = Math.max(constructionThickness(design, surface.kind), 0.02);

    const cells = partition(outer, holes);
    if (cells.length === 0) continue;

    const material = materialFor(surface);
    const meshes: THREE.Mesh[] = [];
    for (const cell of cells) {
      const mesh = placeBox(cell, frame, thickness, 0, material);
      mesh.name = surface.name;
      mesh.userData.surface = surface.name;
      group.add(mesh);
      meshes.push(mesh);
    }
    parts.set(surface.name, meshes);
    built += 1;

    for (const window of windowsByParent.get(surface.name) ?? []) {
      const rect = boundsOf(corners(window), frame);
      const pane = placeBox(
        rect,
        frame,
        Math.min(0.03, thickness * 0.2),
        thickness * 0.45,
        glassFor(window),
      );
      pane.name = window.name;
      pane.userData.surface = window.name;
      pane.castShadow = false;
      group.add(pane);
      parts.set(window.name, [pane]);
    }
  }

  if (built === 0) return null;
  return { group, parts };
}

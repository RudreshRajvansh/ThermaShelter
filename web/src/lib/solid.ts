import * as THREE from "three";
import type { DesignSpec, GeometryPayload, MeshSurface } from "./api";

const EPSILON = 1e-6;

type Frame = {
  origin: THREE.Vector3;
  u: THREE.Vector3;
  v: THREE.Vector3;
  normal: THREE.Vector3;
};

export type SolidBuild = {
  group: THREE.Group;
  parts: Map<string, THREE.Mesh[]>;
  detail: THREE.Group;
};

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
  const unit = normal.clone().normalize();
  let u: THREE.Vector3 | null = null;
  for (let i = 1; i < points.length; i++) {
    const candidate = new THREE.Vector3().subVectors(points[i], points[0]);
    candidate.addScaledVector(unit, -candidate.dot(unit));
    if (candidate.lengthSq() > EPSILON) {
      u = candidate.normalize();
      break;
    }
  }
  if (!u) return null;
  const v = new THREE.Vector3().crossVectors(unit, u).normalize();
  return { origin: points[0].clone(), u, v, normal: unit };
}

function toLocal(point: THREE.Vector3, frame: Frame): THREE.Vector2 {
  const delta = new THREE.Vector3().subVectors(point, frame.origin);
  return new THREE.Vector2(delta.dot(frame.u), delta.dot(frame.v));
}

function unique(points: THREE.Vector2[]): THREE.Vector2[] {
  const out: THREE.Vector2[] = [];
  for (const point of points) {
    const last = out[out.length - 1];
    if (last && last.distanceToSquared(point) < 1e-8) continue;
    out.push(point);
  }
  if (out.length > 1 && out[0].distanceToSquared(out[out.length - 1]) < 1e-8) {
    out.pop();
  }
  return out;
}

function hull(points: THREE.Vector2[]): THREE.Vector2[] {
  if (points.length < 4) return points;
  const sorted = [...points].sort((a, b) => (a.x - b.x) || (a.y - b.y));
  const cross = (o: THREE.Vector2, a: THREE.Vector2, b: THREE.Vector2) =>
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: THREE.Vector2[] = [];
  for (const point of sorted) {
    while (lower.length >= 2 &&
           cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) {
      lower.pop();
    }
    lower.push(point);
  }
  const upper: THREE.Vector2[] = [];
  for (let i = sorted.length - 1; i >= 0; i--) {
    const point = sorted[i];
    while (upper.length >= 2 &&
           cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) {
      upper.pop();
    }
    upper.push(point);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

const FRAME_FRACTION = 0.15;

function frameThickness(): number {
  return FRAME_FRACTION;
}

function constructionThickness(
  design: DesignSpec | null | undefined,
  kind: string,
): number {
  const stack =
    kind === "roof" ? design?.roof
    : kind === "floor" ? design?.floor
    : design?.wall;
  const layers = stack?.layers ?? [];
  const total = layers.reduce(
    (sum: number, layer: { thickness_m: number }) => sum + layer.thickness_m,
    0,
  );
  return total > 0 ? total : 0.3;
}

function place(
  geometry: THREE.BufferGeometry,
  frame: Frame,
  offset: number,
  material: THREE.Material,
): THREE.Mesh {
  const basis = new THREE.Matrix4().makeBasis(frame.u, frame.v, frame.normal);
  basis.setPosition(
    frame.origin.clone().addScaledVector(frame.normal, offset),
  );
  const mesh = new THREE.Mesh(geometry, material);
  mesh.applyMatrix4(basis);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

export function buildSolid(
  geometry: GeometryPayload,
  design: DesignSpec | null | undefined,
  materialFor: (surface: MeshSurface) => THREE.Material,
  glassFor: (surface: MeshSurface) => THREE.Material,
  frameMaterial?: THREE.Material,
): SolidBuild | null {
  const group = new THREE.Group();
  const detail = new THREE.Group();
  const parts = new Map<string, THREE.Mesh[]>();

  const windowsByParent = new Map<string, MeshSurface[]>();
  for (const surface of geometry.surfaces) {
    if (surface.kind !== "window" || !surface.parent) continue;
    const list = windowsByParent.get(surface.parent) ?? [];
    list.push(surface);
    windowsByParent.set(surface.parent, list);
  }

  const mullion = frameMaterial ??
    new THREE.MeshStandardMaterial({
      color: new THREE.Color("#6d6455"),
      roughness: 0.65,
      metalness: 0.1,
    });

  let built = 0;
  for (const surface of geometry.surfaces) {
    if (surface.kind === "window" || surface.kind === "shading") continue;

    const points = corners(surface);
    const normal = new THREE.Vector3(...surface.normal);
    const frame = frameFor(points, normal);
    if (!frame) continue;

    const outline = hull(unique(points.map((point) => toLocal(point, frame))));
    if (outline.length < 3) continue;

    const shape = new THREE.Shape(outline);
    const openings = windowsByParent.get(surface.name) ?? [];
    for (const window of openings) {
      const local = hull(unique(corners(window).map((p) => toLocal(p, frame))));
      if (local.length < 3) continue;
      shape.holes.push(new THREE.Path(local.reverse()));
    }

    const thickness = Math.max(constructionThickness(design, surface.kind), 0.02);
    const extruded = new THREE.ExtrudeGeometry(shape, {
      depth: thickness,
      bevelEnabled: false,
      curveSegments: 1,
    });
    extruded.computeVertexNormals();

    const mesh = place(extruded, frame, -thickness, materialFor(surface));
    mesh.name = surface.name;
    mesh.userData.surface = surface.name;
    group.add(mesh);
    parts.set(surface.name, [mesh]);
    built += 1;

    const border = frameThickness();
    for (const window of openings) {
      const local = hull(unique(corners(window).map((p) => toLocal(p, frame))));
      if (local.length < 3) continue;

      const pane = new THREE.ExtrudeGeometry(new THREE.Shape(local), {
        depth: Math.min(0.028, thickness * 0.25),
        bevelEnabled: false,
        curveSegments: 1,
      });
      pane.computeVertexNormals();
      const glass = place(pane, frame, -thickness * 0.55, glassFor(window));
      glass.name = window.name;
      glass.userData.surface = window.name;
      glass.castShadow = false;
      group.add(glass);
      parts.set(window.name, [glass]);

      const centre = local
        .reduce((sum, p) => sum.add(p), new THREE.Vector2())
        .divideScalar(local.length);
      const inner = local.map((p) =>
        p.clone().sub(centre).multiplyScalar(1 - border).add(centre),
      );
      const ring = new THREE.Shape(local);
      ring.holes.push(new THREE.Path([...inner].reverse()));
      const ringGeometry = new THREE.ExtrudeGeometry(ring, {
        depth: thickness * 0.5,
        bevelEnabled: false,
        curveSegments: 1,
      });
      ringGeometry.computeVertexNormals();
      const sash = place(ringGeometry, frame, -thickness * 0.75, mullion);
      sash.userData.detail = true;
      detail.add(sash);
    }
  }

  if (built === 0) return null;
  group.add(detail);
  return { group, parts, detail };
}

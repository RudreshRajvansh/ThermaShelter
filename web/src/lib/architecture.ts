import * as THREE from "three";
import type { DesignSpec, GeometryPayload, LayoutPayload, MeshSurface } from "./api";

const PLINTH_HEIGHT_M = 0.45;
const PLINTH_OVERHANG_M = 0.12;
const PARAPET_HEIGHT_M = 0.6;
const PARAPET_THICKNESS_M = 0.22;
const COPING_OVERHANG_M = 0.05;
const DOOR_WIDTH_M = 0.9;
const DOOR_HEIGHT_M = 2.05;
const STEP_RISE_M = 0.15;
const STEP_TREAD_M = 0.3;
const RIB_PITCH_M = 0.28;
const RIB_DEPTH_M = 0.035;

export type Architecture = {
  group: THREE.Group;
  elements: string[];
};

function outlineOf(surface: MeshSurface): THREE.Vector2[] {
  const points: THREE.Vector2[] = [];
  for (let i = 0; i < surface.positions.length; i += 3) {
    points.push(new THREE.Vector2(surface.positions[i], surface.positions[i + 1]));
  }
  const seen: THREE.Vector2[] = [];
  for (const point of points) {
    if (seen.some((other) => other.distanceToSquared(point) < 1e-8)) continue;
    seen.push(point);
  }
  return seen;
}

function ordered(points: THREE.Vector2[]): THREE.Vector2[] {
  const centre = points
    .reduce((sum, p) => sum.add(p), new THREE.Vector2())
    .divideScalar(points.length);
  return [...points].sort(
    (a, b) =>
      Math.atan2(a.y - centre.y, a.x - centre.x) -
      Math.atan2(b.y - centre.y, b.x - centre.x),
  );
}

function offsetPolygon(points: THREE.Vector2[], amount: number): THREE.Vector2[] {
  const centre = points
    .reduce((sum, p) => sum.add(p), new THREE.Vector2())
    .divideScalar(points.length);
  return points.map((point) => {
    const radial = new THREE.Vector2().subVectors(point, centre);
    const length = radial.length();
    if (length < 1e-6) return point.clone();
    return radial.multiplyScalar((length + amount) / length).add(centre);
  });
}

function prism(
  outline: THREE.Vector2[],
  base: number,
  height: number,
  material: THREE.Material,
  hole?: THREE.Vector2[],
): THREE.Mesh {
  const shape = new THREE.Shape(outline);
  if (hole) shape.holes.push(new THREE.Path([...hole].reverse()));
  const geometry = new THREE.ExtrudeGeometry(shape, {
    depth: height,
    bevelEnabled: false,
    curveSegments: 1,
  });
  geometry.computeVertexNormals();
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.z = base;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function stoneMaterial(): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color("#7d7468"),
    roughness: 0.97,
    metalness: 0.0,
  });
}

function timberMaterial(): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color("#7a4f2a"),
    roughness: 0.72,
    metalness: 0.03,
  });
}

function metalMaterial(): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color("#9aa6ad"),
    roughness: 0.42,
    metalness: 0.55,
  });
}

function southFacingWall(geometry: GeometryPayload): MeshSurface | null {
  const walls = geometry.surfaces.filter((item) => item.kind === "wall");
  return (
    walls.find((item) => item.facade === "south") ??
    walls.sort((a, b) => b.area_m2 - a.area_m2)[0] ??
    null
  );
}

function roofOuterIsMetal(design: DesignSpec | null | undefined): boolean {
  const layers = design?.roof?.layers ?? [];
  const outer = layers[0];
  return typeof outer?.material_id === "string" &&
    /gi_sheet|steel|metal/.test(outer.material_id);
}

export function buildArchitecture(
  geometry: GeometryPayload,
  design: DesignSpec | null | undefined,
  layout: LayoutPayload | null | undefined,
): Architecture | null {
  const floor = geometry.surfaces.find((item) => item.kind === "floor");
  const roof = geometry.surfaces.find((item) => item.kind === "roof");
  if (!floor || !roof) return null;

  const group = new THREE.Group();
  const elements: string[] = [];

  const footprint = ordered(outlineOf(floor));
  if (footprint.length < 3) return null;

  const floorZ = floor.positions[2] ?? 0;
  const roofZ = roof.positions[2] ?? floorZ + 2.6;

  const stone = stoneMaterial();
  const timber = timberMaterial();
  const metal = metalMaterial();

  const plinth = prism(
    offsetPolygon(footprint, PLINTH_OVERHANG_M),
    floorZ - PLINTH_HEIGHT_M,
    PLINTH_HEIGHT_M,
    stone,
  );
  plinth.userData.detail = "plinth";
  group.add(plinth);
  elements.push("stone plinth");

  const parapetOuter = offsetPolygon(footprint, 0.01);
  const parapetInner = offsetPolygon(footprint, -PARAPET_THICKNESS_M);
  const parapet = prism(
    parapetOuter,
    roofZ,
    PARAPET_HEIGHT_M,
    stone,
    parapetInner,
  );
  parapet.userData.detail = "parapet";
  group.add(parapet);

  const coping = prism(
    offsetPolygon(footprint, COPING_OVERHANG_M),
    roofZ + PARAPET_HEIGHT_M,
    0.06,
    stone,
    offsetPolygon(footprint, -PARAPET_THICKNESS_M - COPING_OVERHANG_M),
  );
  coping.userData.detail = "coping";
  group.add(coping);
  elements.push("roof parapet and coping");

  const wall = southFacingWall(geometry);
  if (wall) {
    const points: THREE.Vector3[] = [];
    for (let i = 0; i < wall.positions.length; i += 3) {
      points.push(
        new THREE.Vector3(
          wall.positions[i],
          wall.positions[i + 1],
          wall.positions[i + 2],
        ),
      );
    }
    const normal = new THREE.Vector3(...wall.normal).normalize();
    let along: THREE.Vector3 | null = null;
    for (let i = 0; i < points.length && !along; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const edge = new THREE.Vector3().subVectors(points[j], points[i]);
        edge.z = 0;
        if (edge.lengthSq() > 1e-4) {
          along = edge.normalize();
          break;
        }
      }
    }
    if (along) {
      const centre = points
        .reduce((sum, p) => sum.add(p), new THREE.Vector3())
        .divideScalar(points.length);
      const anchor = new THREE.Vector3(
        centre.x,
        centre.y,
        floorZ + DOOR_HEIGHT_M / 2,
      ).addScaledVector(along, -2.2);

      const door = new THREE.Mesh(
        new THREE.BoxGeometry(DOOR_WIDTH_M, 0.08, DOOR_HEIGHT_M),
        timber,
      );
      door.position.copy(anchor).addScaledVector(normal, 0.06);
      door.quaternion.setFromUnitVectors(
        new THREE.Vector3(1, 0, 0),
        along,
      );
      door.castShadow = true;
      door.receiveShadow = true;
      door.userData.detail = "door";
      group.add(door);
      elements.push("entrance door");

      for (let step = 0; step < Math.ceil(PLINTH_HEIGHT_M / STEP_RISE_M); step++) {
        const rise = PLINTH_HEIGHT_M - step * STEP_RISE_M;
        const tread = new THREE.Mesh(
          new THREE.BoxGeometry(DOOR_WIDTH_M + 0.5, STEP_TREAD_M, STEP_RISE_M),
          stone,
        );
        tread.position
          .copy(anchor)
          .setZ(floorZ - rise + STEP_RISE_M / 2)
          .addScaledVector(normal, 0.2 + step * STEP_TREAD_M);
        tread.quaternion.setFromUnitVectors(new THREE.Vector3(1, 0, 0), along);
        tread.castShadow = true;
        tread.receiveShadow = true;
        tread.userData.detail = "step";
        group.add(tread);
      }
      elements.push("entry steps");
    }
  }

  if (roofOuterIsMetal(design)) {
    const box = new THREE.Box2();
    for (const point of footprint) box.expandByPoint(point);
    const size = box.getSize(new THREE.Vector2());
    const count = Math.max(2, Math.floor(size.x / RIB_PITCH_M));
    for (let i = 0; i < count; i++) {
      const rib = new THREE.Mesh(
        new THREE.BoxGeometry(RIB_DEPTH_M, size.y - PARAPET_THICKNESS_M * 2, RIB_DEPTH_M),
        metal,
      );
      rib.position.set(
        box.min.x + (i + 0.5) * (size.x / count),
        box.min.y + size.y / 2,
        roofZ + RIB_DEPTH_M / 2,
      );
      rib.castShadow = true;
      rib.receiveShadow = true;
      rib.userData.detail = "rib";
      group.add(rib);
    }
    elements.push("corrugated roof deck");
  }

  if (layout && layout.rooms.length > 1) {
    elements.push(`${layout.rooms.length} room layout`);
  }

  return { group, elements };
}

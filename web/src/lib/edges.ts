import * as THREE from "three";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";

export type EdgeLines = {
  object: LineSegments2;
  setResolution: (width: number, height: number) => void;
  setColour: (colour: THREE.ColorRepresentation) => void;
  setWidth: (pixels: number) => void;
  setOpacity: (value: number) => void;
  dispose: () => void;
};

function segmentsFrom(meshes: Iterable<THREE.Mesh>, threshold: number): number[] {
  const points: number[] = [];
  const vertex = new THREE.Vector3();
  for (const mesh of meshes) {
    if (!mesh.geometry) continue;
    mesh.updateWorldMatrix(true, false);
    const edges = new THREE.EdgesGeometry(mesh.geometry, threshold);
    const position = edges.getAttribute("position");
    for (let index = 0; index < position.count; index += 1) {
      vertex.fromBufferAttribute(position as THREE.BufferAttribute, index);
      vertex.applyMatrix4(mesh.matrixWorld);
      points.push(vertex.x, vertex.y, vertex.z);
    }
    edges.dispose();
  }
  return points;
}

export function createEdgeLines(
  meshes: Iterable<THREE.Mesh>,
  options: {
    colour: THREE.ColorRepresentation;
    widthPx: number;
    threshold?: number;
    clippingPlanes?: THREE.Plane[];
    opacity?: number;
  },
): EdgeLines | null {
  const points = segmentsFrom(meshes, options.threshold ?? 24);
  if (points.length < 6) return null;

  const geometry = new LineSegmentsGeometry();
  geometry.setPositions(points);

  const material = new LineMaterial({
    color: new THREE.Color(options.colour).getHex(),
    linewidth: options.widthPx,
    worldUnits: false,
    transparent: true,
    opacity: options.opacity ?? 1,
    depthTest: true,
    alphaToCoverage: false,
    clippingPlanes: options.clippingPlanes ?? [],
  });
  material.resolution.set(1, 1);

  const object = new LineSegments2(geometry, material);
  object.computeLineDistances();
  object.frustumCulled = false;
  object.renderOrder = 3;

  return {
    object,
    setResolution: (width: number, height: number) => {
      material.resolution.set(Math.max(width, 1), Math.max(height, 1));
    },
    setColour: (colour: THREE.ColorRepresentation) => {
      material.color.set(colour);
    },
    setWidth: (pixels: number) => {
      material.linewidth = pixels;
    },
    setOpacity: (value: number) => {
      material.transparent = value < 1;
      material.opacity = value;
    },
    dispose: () => {
      geometry.dispose();
      material.dispose();
    },
  };
}

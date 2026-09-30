import * as THREE from "three";

export const FIGURE_HEIGHT_M = 1.75;

function silhouette(): THREE.Shape {
  const shape = new THREE.Shape();
  const unit = FIGURE_HEIGHT_M;
  const points: [number, number][] = [
    [0.0, 0.0], [0.055, 0.0], [0.062, 0.26], [0.086, 0.26], [0.082, 0.0],
    [0.14, 0.0], [0.132, 0.3], [0.124, 0.52], [0.166, 0.53], [0.176, 0.61],
    [0.138, 0.62], [0.13, 0.7], [0.106, 0.78], [0.078, 0.82],
    [0.072, 0.86], [0.05, 0.9], [0.0, 0.912],
  ];
  const mirrored: [number, number][] = points
    .slice(0, -1)
    .reverse()
    .map(([x, y]) => [-x, y]);
  const all = [...points, ...mirrored];
  shape.moveTo(all[0][0] * unit, all[0][1] * unit);
  for (const [x, y] of all.slice(1)) shape.lineTo(x * unit, y * unit);
  shape.closePath();
  return shape;
}

export type ScaleFigure = {
  group: THREE.Group;
  faceCamera: (camera: THREE.Camera) => void;
  setColour: (colour: THREE.ColorRepresentation) => void;
  dispose: () => void;
};

export function createScaleFigure(position: THREE.Vector3,
                                  colour: THREE.ColorRepresentation): ScaleFigure {
  const group = new THREE.Group();
  group.position.copy(position);

  const geometry = new THREE.ShapeGeometry(silhouette(), 12);
  geometry.rotateX(Math.PI / 2);
  const material = new THREE.MeshBasicMaterial({
    color: new THREE.Color(colour),
    transparent: true,
    opacity: 0.72,
    side: THREE.DoubleSide,
    toneMapped: false,
    depthWrite: false,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.renderOrder = 5;
  group.add(mesh);

  const forward = new THREE.Vector3();
  return {
    group,
    faceCamera: (camera) => {
      forward.subVectors(camera.position, group.position);
      group.rotation.z = Math.atan2(forward.y, forward.x) - Math.PI / 2;
    },
    setColour: (next) => material.color.set(next),
    dispose: () => {
      geometry.dispose();
      material.dispose();
    },
  };
}

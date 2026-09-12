import * as THREE from "three";

export type SectionCaps = {
  group: THREE.Group;
  update: () => void;
  setVisible: (visible: boolean) => void;
  setColours: (colourOf: (name: string) => THREE.ColorRepresentation) => void;
  dispose: () => void;
};

function stencilMaterial(plane: THREE.Plane, side: THREE.Side,
                         operation: THREE.StencilOp): THREE.MeshBasicMaterial {
  const material = new THREE.MeshBasicMaterial({
    depthWrite: false,
    depthTest: false,
    colorWrite: false,
    stencilWrite: true,
    stencilFunc: THREE.AlwaysStencilFunc,
  });
  material.side = side;
  material.clippingPlanes = [plane];
  material.stencilFail = operation;
  material.stencilZFail = operation;
  material.stencilZPass = operation;
  return material;
}

export function createSectionCaps(
  parts: Iterable<{ name: string; mesh: THREE.Mesh }>,
  plane: THREE.Plane,
  span: number,
  colourOf: (name: string) => THREE.ColorRepresentation,
): SectionCaps {
  const group = new THREE.Group();
  const planeGeometry = new THREE.PlaneGeometry(span, span);
  const materials: THREE.Material[] = [];
  const caps: { name: string; mesh: THREE.Mesh; material: THREE.MeshStandardMaterial }[] = [];
  let order = 1;

  for (const part of parts) {
    if (!part.mesh.geometry) continue;

    const back = stencilMaterial(plane, THREE.BackSide, THREE.IncrementWrapStencilOp);
    const front = stencilMaterial(plane, THREE.FrontSide, THREE.DecrementWrapStencilOp);
    materials.push(back, front);

    for (const material of [back, front]) {
      const stencil = new THREE.Mesh(part.mesh.geometry, material);
      stencil.applyMatrix4(part.mesh.matrix);
      stencil.renderOrder = order;
      stencil.raycast = () => undefined;
      group.add(stencil);
    }

    const capMaterial = new THREE.MeshStandardMaterial({
      color: new THREE.Color(colourOf(part.name)),
      metalness: 0.0,
      roughness: 0.82,
      side: THREE.DoubleSide,
      stencilWrite: true,
      stencilRef: 0,
      stencilFunc: THREE.NotEqualStencilFunc,
      stencilFail: THREE.ReplaceStencilOp,
      stencilZFail: THREE.ReplaceStencilOp,
      stencilZPass: THREE.ReplaceStencilOp,
    });
    const cap = new THREE.Mesh(planeGeometry, capMaterial);
    cap.onAfterRender = (renderer) => renderer.clearStencil();
    cap.renderOrder = order + 0.1;
    cap.raycast = () => undefined;
    group.add(cap);
    caps.push({ name: part.name, mesh: cap, material: capMaterial });
    order += 1;
  }

  const point = new THREE.Vector3();
  const update = () => {
    plane.coplanarPoint(point);
    point.sub(group.position);
    for (const cap of caps) {
      cap.mesh.position.copy(point);
      cap.mesh.lookAt(
        point.x - plane.normal.x,
        point.y - plane.normal.y,
        point.z - plane.normal.z,
      );
    }
  };
  update();

  return {
    group,
    update,
    setVisible: (visible: boolean) => {
      group.visible = visible;
    },
    setColours: (next) => {
      for (const cap of caps) cap.material.color.set(next(cap.name));
    },
    dispose: () => {
      planeGeometry.dispose();
      for (const material of materials) material.dispose();
      for (const cap of caps) cap.material.dispose();
    },
  };
}

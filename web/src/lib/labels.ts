import * as THREE from "three";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";

export type LabelKind = "cardinal" | "dimension" | "note";

export type LabelLayer = {
  element: HTMLElement;
  group: THREE.Group;
  render: (scene: THREE.Scene, camera: THREE.Camera) => void;
  resize: (width: number, height: number) => void;
  clear: () => void;
  add: (text: string, position: THREE.Vector3, kind: LabelKind, title?: string) => CSS2DObject;
  paint: (context: CanvasRenderingContext2D, camera: THREE.Camera, width: number,
          height: number, scale: number) => void;
  dispose: () => void;
};

export function createLabelLayer(): LabelLayer {
  const renderer = new CSS2DRenderer();
  renderer.domElement.className = "scene-labels";
  const group = new THREE.Group();
  group.name = "labels";

  const add = (text: string, position: THREE.Vector3, kind: LabelKind,
               title?: string): CSS2DObject => {
    const element = document.createElement("span");
    element.className = `scene-label scene-label-${kind}`;
    element.textContent = text;
    if (title) element.title = title;
    const object = new CSS2DObject(element);
    object.position.copy(position);
    object.center.set(0.5, 0.5);
    group.add(object);
    return object;
  };

  const clear = () => {
    for (const child of [...group.children]) {
      const label = child as CSS2DObject;
      label.element.remove();
      group.remove(label);
    }
  };

  const projected = new THREE.Vector3();

  return {
    element: renderer.domElement,
    group,
    render: (scene, camera) => renderer.render(scene, camera),
    paint: (context, camera, width, height, scale) => {
      for (const child of group.children) {
        const label = child as CSS2DObject;
        if (!label.visible || !label.element.textContent) continue;
        label.getWorldPosition(projected);
        projected.project(camera);
        if (projected.z > 1) continue;
        const x = (projected.x * 0.5 + 0.5) * width;
        const y = (-projected.y * 0.5 + 0.5) * height;
        const style = getComputedStyle(label.element);
        const size = parseFloat(style.fontSize) * scale;
        context.font = `${style.fontWeight} ${size}px ${style.fontFamily}`;
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.lineWidth = 3 * scale;
        context.lineJoin = "round";
        context.strokeStyle = "rgba(255, 255, 255, 0.92)";
        context.strokeText(label.element.textContent, x, y);
        context.fillStyle = style.color;
        context.fillText(label.element.textContent, x, y);
      }
    },
    resize: (width, height) => renderer.setSize(width, height),
    clear,
    add,
    dispose: () => {
      clear();
      renderer.domElement.remove();
    },
  };
}

export function cardinalPoints(radius: number): { text: string; position: THREE.Vector3 }[] {
  return [
    { text: "N", azimuth: 0 },
    { text: "E", azimuth: 90 },
    { text: "S", azimuth: 180 },
    { text: "W", azimuth: 270 },
  ].map((mark) => {
    const angle = THREE.MathUtils.degToRad(mark.azimuth);
    return {
      text: mark.text,
      position: new THREE.Vector3(radius * Math.sin(angle), radius * Math.cos(angle), 0.6),
    };
  });
}

export function dimensionLines(width: number, depth: number, height: number,
                               colour: THREE.ColorRepresentation): {
  object: THREE.LineSegments;
  labels: { text: string; position: THREE.Vector3; title: string }[];
} {
  const halfWidth = width / 2;
  const halfDepth = depth / 2;
  const gap = Math.max(0.6, Math.min(width, depth) * 0.12);
  const y = -halfDepth - gap;
  const x = halfWidth + gap;
  const tick = gap * 0.35;

  const points: THREE.Vector3[] = [
    new THREE.Vector3(-halfWidth, y, 0), new THREE.Vector3(halfWidth, y, 0),
    new THREE.Vector3(-halfWidth, y - tick, 0), new THREE.Vector3(-halfWidth, y + tick, 0),
    new THREE.Vector3(halfWidth, y - tick, 0), new THREE.Vector3(halfWidth, y + tick, 0),
    new THREE.Vector3(x, -halfDepth, 0), new THREE.Vector3(x, halfDepth, 0),
    new THREE.Vector3(x - tick, -halfDepth, 0), new THREE.Vector3(x + tick, -halfDepth, 0),
    new THREE.Vector3(x - tick, halfDepth, 0), new THREE.Vector3(x + tick, halfDepth, 0),
    new THREE.Vector3(x, y, 0), new THREE.Vector3(x, y, height),
    new THREE.Vector3(x - tick, height, 0), new THREE.Vector3(x + tick, height, 0),
  ];
  points[14].set(x - tick, y, height);
  points[15].set(x + tick, y, height);

  const object = new THREE.LineSegments(
    new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: 0.85,
                                  toneMapped: false }),
  );
  object.renderOrder = 4;

  return {
    object,
    labels: [
      {
        text: `${width.toFixed(2)} m`,
        position: new THREE.Vector3(0, y - tick * 1.6, 0),
        title: "Outside width along the east to west axis, from the design you entered.",
      },
      {
        text: `${depth.toFixed(2)} m`,
        position: new THREE.Vector3(x + tick * 1.6, 0, 0),
        title: "Outside depth along the north to south axis, from the design you entered.",
      },
      {
        text: `${height.toFixed(2)} m`,
        position: new THREE.Vector3(x + tick * 1.6, y, height / 2),
        title: "Floor to ceiling height, from the design you entered.",
      },
    ],
  };
}

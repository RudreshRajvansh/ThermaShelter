const BAR_STEPS = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];

export type ScaleBar = { metres: number; pixels: number };

export function scaleBar(metresPerPixel: number): ScaleBar | null {
  if (!Number.isFinite(metresPerPixel) || metresPerPixel <= 0) return null;
  for (const metres of BAR_STEPS) {
    const pixels = metres / metresPerPixel;
    if (pixels >= 72 && pixels <= 190) return { metres, pixels };
  }
  const metres = BAR_STEPS[BAR_STEPS.length - 1];
  return { metres, pixels: metres / metresPerPixel };
}

export function barLabel(metres: number): string {
  return metres >= 1000 ? `${metres / 1000} km` : `${metres} m`;
}

export function paintHud(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  scale: number,
  compass: { metresPerPixel: number; northDeg: number },
): void {
  const bar = scaleBar(compass.metresPerPixel);
  const margin = 16 * scale;
  const dialRadius = 17 * scale;
  const dialX = width - margin - dialRadius;
  const dialY = height - margin - dialRadius;

  context.save();
  context.lineJoin = "round";

  if (bar) {
    const length = bar.pixels * scale;
    const right = dialX - dialRadius - 12 * scale;
    const left = right - length;
    const y = height - margin - 5 * scale;

    context.fillStyle = "#1f2733";
    context.fillRect(left, y, length / 2, 4 * scale);
    context.fillStyle = "rgba(255,255,255,0.92)";
    context.fillRect(left + length / 2, y, length / 2, 4 * scale);
    context.strokeStyle = "#1f2733";
    context.lineWidth = 1.2 * scale;
    context.strokeRect(left + length / 2, y, length / 2, 4 * scale);
    context.beginPath();
    context.moveTo(left, y - 3 * scale);
    context.lineTo(left, y + 7 * scale);
    context.moveTo(right, y - 3 * scale);
    context.lineTo(right, y + 7 * scale);
    context.stroke();

    context.font = `700 ${11 * scale}px ui-sans-serif, system-ui, sans-serif`;
    context.textAlign = "left";
    context.textBaseline = "alphabetic";
    const text = barLabel(bar.metres);
    const box = context.measureText(text);
    context.fillStyle = "rgba(255,255,255,0.85)";
    context.fillRect(left - 2 * scale, y - 20 * scale, box.width + 5 * scale, 15 * scale);
    context.fillStyle = "#1f2733";
    context.fillText(text, left, y - 9 * scale);
  }

  context.translate(dialX, dialY);
  context.beginPath();
  context.arc(0, 0, dialRadius, 0, Math.PI * 2);
  context.fillStyle = "rgba(255,255,255,0.85)";
  context.fill();
  context.strokeStyle = "#5d6b7d";
  context.lineWidth = 1.1 * scale;
  context.stroke();

  context.rotate((compass.northDeg * Math.PI) / 180);
  context.beginPath();
  context.moveTo(0, -dialRadius * 0.92);
  context.lineTo(dialRadius * 0.3, dialRadius * 0.2);
  context.lineTo(0, dialRadius * 0.02);
  context.lineTo(-dialRadius * 0.3, dialRadius * 0.2);
  context.closePath();
  context.fillStyle = "#b4402f";
  context.fill();
  context.beginPath();
  context.moveTo(0, dialRadius * 0.35);
  context.lineTo(0, dialRadius * 0.9);
  context.strokeStyle = "#5d6b7d";
  context.lineWidth = 1.4 * scale;
  context.stroke();
  context.restore();
}

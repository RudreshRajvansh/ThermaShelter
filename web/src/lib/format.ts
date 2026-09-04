export function num(
  value: number | null | undefined,
  digits = 1,
  fallback = "—",
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return fallback;
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function signed(
  value: number | null | undefined,
  digits = 1,
  fallback = "—",
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return fallback;
  const text = num(Math.abs(value), digits);
  return `${value >= 0 ? "+" : "−"}${text}`;
}

export function rupees(value: number | null | undefined, fallback = "—"): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return fallback;
  return `₹${Math.round(value).toLocaleString("en-IN")}`;
}

export function clockLabel(iso: string): string {
  return iso.slice(11, 16);
}

export function dayLabel(iso: string): string {
  return iso.slice(0, 10);
}

export function hourOf(iso: string): number {
  return Number(iso.slice(11, 13));
}

const SEVERITY_ORDER: Record<string, number> = {
  fatal: 0,
  severe: 1,
  warning: 2,
  info: 3,
};

export function bySeverity<T extends { severity: string }>(items: T[]): T[] {
  return [...items].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
  );
}

export function lerpColour(t: number, stops: [number, number, number][]): string {
  const clamped = Math.max(0, Math.min(1, t));
  const scaled = clamped * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  const local = scaled - index;
  const a = stops[index];
  const b = stops[index + 1];
  const channel = (i: number) => Math.round(a[i] + (b[i] - a[i]) * local);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

export const COLD_TO_HOT: [number, number, number][] = [
  [28, 58, 112],
  [43, 104, 172],
  [94, 164, 208],
  [168, 208, 225],
  [238, 238, 230],
  [244, 200, 128],
  [230, 148, 62],
  [199, 88, 44],
  [150, 34, 30],
];

export const SOLAR_SCALE: [number, number, number][] = [
  [22, 30, 48],
  [52, 60, 96],
  [104, 92, 128],
  [163, 118, 96],
  [211, 154, 62],
  [244, 197, 66],
  [253, 231, 130],
  [255, 250, 224],
];

export const TURBO: [number, number, number][] = [
  [48, 18, 59],
  [64, 74, 187],
  [42, 141, 236],
  [30, 197, 199],
  [70, 235, 128],
  [163, 252, 60],
  [231, 220, 55],
  [253, 158, 47],
  [237, 84, 20],
  [178, 27, 5],
  [122, 4, 3],
];

export const ELEVATION: [number, number, number][] = [
  [122, 106, 78],
  [148, 126, 88],
  [161, 138, 104],
  [143, 130, 116],
  [126, 122, 120],
  [148, 148, 148],
  [186, 188, 192],
  [222, 226, 232],
  [246, 248, 252],
];

export const RAMPS: Record<string, [number, number, number][]> = {
  diverging: COLD_TO_HOT,
  solar: SOLAR_SCALE,
  spectral: TURBO,
};

export function rampRgb(
  t: number,
  stops: [number, number, number][],
): [number, number, number] {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
  const scaled = clamped * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  const local = scaled - index;
  const a = stops[index];
  const b = stops[index + 1];
  return [
    a[0] + (b[0] - a[0]) * local,
    a[1] + (b[1] - a[1]) * local,
    a[2] + (b[2] - a[2]) * local,
  ];
}

export function rampCss(stops: [number, number, number][], steps = 12): string {
  const parts: string[] = [];
  for (let i = 0; i < steps; i++) {
    const t = i / (steps - 1);
    const [r, g, b] = rampRgb(t, stops);
    parts.push(
      `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}) ${(t * 100).toFixed(1)}%`,
    );
  }
  return `linear-gradient(to right, ${parts.join(", ")})`;
}

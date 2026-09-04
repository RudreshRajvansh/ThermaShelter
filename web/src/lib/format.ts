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
  [43, 84, 148],
  [90, 154, 200],
  [232, 232, 226],
  [222, 150, 78],
  [176, 58, 46],
];

export const SOLAR_SCALE: [number, number, number][] = [
  [30, 41, 59],
  [88, 92, 120],
  [196, 152, 62],
  [252, 211, 77],
  [255, 247, 214],
];

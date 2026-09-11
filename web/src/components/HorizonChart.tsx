import { num } from "../lib/format";

const COMPASS = [
  { label: "N", azimuth: 0 },
  { label: "E", azimuth: 90 },
  { label: "S", azimuth: 180 },
  { label: "W", azimuth: 270 },
];

export function HorizonChart({
  azimuths,
  blocked,
  size = 168,
}: {
  azimuths: number[];
  blocked: number[];
  size?: number;
}) {
  if (azimuths.length < 2 || azimuths.length !== blocked.length) return null;
  const centre = size / 2;
  const radius = size / 2 - 18;
  const toPoint = (azimuth: number, altitude: number) => {
    const distance = radius * (1 - Math.min(Math.max(altitude, 0), 90) / 90);
    const angle = (azimuth * Math.PI) / 180;
    return [centre + distance * Math.sin(angle), centre - distance * Math.cos(angle)];
  };
  const skyline = azimuths
    .map((azimuth, index) => toPoint(azimuth, blocked[index]).join(","))
    .join(" ");
  const peak = blocked.reduce((best, value, index) => (value > blocked[best] ? index : best), 0);

  return (
    <figure className="flex flex-col items-center gap-2">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="img"
        aria-label={`Horizon profile, blocked up to ${num(blocked[peak], 1)} degrees toward ${num(azimuths[peak], 0)} degrees`}
      >
        <circle cx={centre} cy={centre} r={radius} fill="#c9b79c" />
        <polygon points={skyline} fill="#e8f1fb" stroke="#7a6a52" strokeWidth={1} />
        {[30, 60].map((altitude) => (
          <circle
            key={altitude}
            cx={centre}
            cy={centre}
            r={radius * (1 - altitude / 90)}
            fill="none"
            stroke="#b6c7da"
            strokeDasharray="2 3"
          />
        ))}
        <circle cx={centre} cy={centre} r={2} fill="#475569" />
        {COMPASS.map((item) => {
          const [x, y] = toPoint(item.azimuth, -12);
          return (
            <text
              key={item.label}
              x={x}
              y={y}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={10}
              fill="#475569"
            >
              {item.label}
            </text>
          );
        })}
      </svg>
      <figcaption className="max-w-[16rem] text-center text-[11px] leading-snug text-[var(--color-ink-faint)]">
        Overhead is the centre and the edge is the horizon. The brown band is terrain standing
        above it, highest at {num(blocked[peak], 1)}° toward {num(azimuths[peak], 0)}°.
      </figcaption>
    </figure>
  );
}

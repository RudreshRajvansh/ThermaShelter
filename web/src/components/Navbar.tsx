import { useQuery } from "@tanstack/react-query";
import { api, type MetaResponse } from "../lib/api";
import { useStore, type ThemePreference } from "../lib/store";
import { num } from "../lib/format";

function Dot({ filled }: { filled: boolean }) {
  return (
    <svg viewBox="0 0 10 10" width={8} height={8} aria-hidden="true">
      <circle
        cx={5}
        cy={5}
        r={3.6}
        fill={filled ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth={1.6}
      />
    </svg>
  );
}

function ThemeIcon({ theme }: { theme: ThemePreference }) {
  const common = {
    width: 15,
    height: 15,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (theme === "light") {
    return (
      <svg {...common}>
        <circle cx={12} cy={12} r={4} />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  }
  if (theme === "dark") {
    return (
      <svg {...common}>
        <path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5a8.5 8.5 0 1 0 10.7 10.7z" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <rect x={3} y={4} width={18} height={12} rx={2} />
      <path d="M8 20h8M12 16v4" />
    </svg>
  );
}

const THEMES: { value: ThemePreference; label: string }[] = [
  { value: "system", label: "Match the system" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

function ThemeToggle() {
  const store = useStore();
  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="flex items-center gap-0.5 rounded-xl border border-[var(--color-line)] bg-[var(--color-night-2)]/70 p-1 shadow-sm backdrop-blur-md"
    >
      {THEMES.map((item) => {
        const active = store.theme === item.value;
        return (
          <button
            key={item.value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={item.label}
            title={item.label}
            onClick={() => store.setTheme(item.value)}
            className={`flex h-7 w-8 items-center justify-center rounded-lg transition-colors duration-150 ${
              active
                ? "bg-[var(--color-night-3)] text-[var(--color-ink)] shadow-sm"
                : "text-[var(--color-ink-faint)] hover:bg-[var(--color-night-3)]/60 hover:text-[var(--color-ink)]"
            }`}
          >
            <ThemeIcon theme={item.value} />
          </button>
        );
      })}
    </div>
  );
}

function ModeToggle() {
  const store = useStore();
  const live = useQuery({
    queryKey: ["live-weather", store.siteId],
    queryFn: () => api.liveWeather(store.siteId),
    enabled: store.liveMode && Boolean(store.siteId),
    refetchInterval: 300000,
    retry: false,
  });

  const observed = live.data?.observation?.air_temperature_c;
  const reading =
    live.data?.available && typeof observed === "number"
      ? `${observed.toFixed(1)} °C now`
      : live.data && !live.data.available
        ? "unreachable"
        : live.isFetching
          ? "fetching…"
          : null;

  return (
    <div className="flex items-center gap-1 rounded-xl border border-[var(--color-line)] bg-[var(--color-night-2)]/70 p-1 text-[13px] shadow-sm backdrop-blur-md">
      <button
        type="button"
        onClick={() => store.setLiveMode(false)}
        aria-pressed={!store.liveMode}
        className={`flex items-center gap-2 rounded-lg px-3 py-1.5 transition-colors duration-150 ${
          !store.liveMode
            ? "bg-[var(--color-good)]/15 font-medium text-[var(--color-good)] shadow-sm"
            : "text-[var(--color-ink-faint)] hover:bg-[var(--color-night-3)]/60"
        }`}
        title="Simulations always run against the stored weather year of the selected site, with no network."
      >
        <Dot filled={!store.liveMode} />
        Stored weather
      </button>
      <button
        type="button"
        onClick={() => store.setLiveMode(true)}
        aria-pressed={store.liveMode}
        title="Current conditions at the site, for context only. The simulation never reads them."
        className={`flex items-center gap-2 rounded-lg px-3 py-1.5 transition-colors duration-150 ${
          store.liveMode
            ? "bg-[var(--color-cold)]/15 font-medium text-[var(--color-cold)] shadow-sm"
            : "text-[var(--color-ink-faint)] hover:bg-[var(--color-night-3)]/60"
        }`}
      >
        <Dot filled={store.liveMode} />
        Live{reading ? ` · ${reading}` : ""}
      </button>
    </div>
  );
}

export function Navbar({ meta, ready }: { meta: MetaResponse | undefined; ready: boolean }) {
  const store = useStore();
  const site = meta?.sites.find((item) => item.id === store.siteId);
  const steps = [
    { page: "input" as const, label: "Inputs", enabled: true },
    { page: "output" as const, label: "Results", enabled: ready },
    { page: "operate" as const, label: "Operate", enabled: true },
  ];

  return (
    <header className="no-print sticky top-0 z-[1000] border-b border-[var(--color-line)]/60 bg-[var(--color-night-2)]/75 shadow-sm backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1360px] flex-wrap items-center gap-x-6 gap-y-3 px-5 py-3">
        <div>
          <p className="display text-lg font-semibold leading-none tracking-tight text-[var(--color-ink)]">
            Entropy Zero
          </p>
          <p className="mt-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-ink-faint)]">
            DRDO · passive shelter design
          </p>
        </div>

        <nav className="flex items-center gap-1" aria-label="Pages">
          {steps.map((step, index) => {
            const active = store.page === step.page;
            return (
              <div key={step.page} className="flex items-center">
                <button
                  type="button"
                  disabled={!step.enabled}
                  aria-current={active ? "page" : undefined}
                  onClick={() =>
                    step.page === "output" ? store.open(store.outputTab) : store.setPage(step.page)
                  }
                  className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-[13px] transition-colors duration-150 disabled:opacity-40 ${
                    active
                      ? "border-[var(--color-solar)] bg-[var(--color-solar)]/12 font-medium text-[var(--color-solar-ink)]"
                      : "border-transparent text-[var(--color-ink-dim)] hover:bg-[var(--color-line-soft)]"
                  }`}
                >
                  <span
                    className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] ${
                      active ? "bg-[var(--color-solar)] text-white" : "border border-[var(--color-line)]"
                    }`}
                  >
                    {index + 1}
                  </span>
                  {step.label}
                </button>
                {index < steps.length - 1 && (
                  <span className="mx-1 h-px w-4 bg-[var(--color-line)]" />
                )}
              </div>
            );
          })}
        </nav>

        <div className="ml-auto flex flex-wrap items-center gap-3">
          {site && (
            <span className="tnum hidden rounded-lg border border-[var(--color-line-soft)] bg-[var(--color-night-2)]/80 px-3 py-1.5 text-[12px] text-[var(--color-ink-dim)] md:inline">
              <span className="font-medium text-[var(--color-ink)]">{site.name}</span> ·{" "}
              {num(site.elevation_m, 0)} m
            </span>
          )}
          <ModeToggle />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

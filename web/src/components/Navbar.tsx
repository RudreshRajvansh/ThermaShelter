import { useQuery } from "@tanstack/react-query";
import { api, type MetaResponse } from "../lib/api";
import { useStore } from "../lib/store";
import { num } from "../lib/format";

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
    <div className="flex items-center gap-1 rounded-xl border border-[var(--color-line)] bg-white/70 p-1 text-[13px] shadow-sm backdrop-blur-md">
      <button
        type="button"
        onClick={() => store.setLiveMode(false)}
        className={`rounded-lg px-3 py-1.5 transition-colors ${
          !store.liveMode
            ? "bg-[var(--color-good)]/15 font-medium text-[var(--color-good)] shadow-sm"
            : "text-[var(--color-ink-faint)] hover:bg-white"
        }`}
        title="Simulations always run against the stored weather year of the selected site, with no network."
      >
        ● Stored weather
      </button>
      <button
        type="button"
        onClick={() => store.setLiveMode(true)}
        title="Current conditions at the site, for context only. The simulation never reads them."
        className={`rounded-lg px-3 py-1.5 transition-colors ${
          store.liveMode
            ? "bg-[var(--color-cold)]/15 font-medium text-[var(--color-cold)] shadow-sm"
            : "text-[var(--color-ink-faint)] hover:bg-white"
        }`}
      >
        ○ Live{reading ? ` · ${reading}` : ""}
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
  ];

  return (
    <header className="no-print sticky top-0 z-[1000] border-b border-white/20 bg-white/70 shadow-sm backdrop-blur-xl">
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
                  onClick={() => (step.page === "input" ? store.setPage("input") : store.open(store.outputTab))}
                  className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-[13px] transition disabled:opacity-40 ${
                    active
                      ? "border-[var(--color-solar)] bg-[var(--color-solar)]/12 font-medium text-[var(--color-solar)]"
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
                {index === 0 && <span className="mx-1 h-px w-4 bg-[var(--color-line)]" />}
              </div>
            );
          })}
        </nav>

        <div className="ml-auto flex flex-wrap items-center gap-3">
          {site && (
            <span className="tnum hidden rounded-lg border border-[var(--color-line-soft)] bg-white/80 px-3 py-1.5 text-[12px] text-[var(--color-ink-dim)] md:inline">
              <span className="font-medium text-[var(--color-ink)]">{site.name}</span> ·{" "}
              {num(site.elevation_m, 0)} m
            </span>
          )}
          <ModeToggle />
        </div>
      </div>
    </header>
  );
}

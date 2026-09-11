import { useQuery } from "@tanstack/react-query";
import { api, type MetaResponse } from "../lib/api";
import { useStore } from "../lib/store";

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
    <div className="flex items-center gap-1 rounded-lg border border-[var(--color-line)] bg-white p-1 text-xs shadow-sm">
      <button
        type="button"
        onClick={() => store.setLiveMode(false)}
        className={`rounded-md px-3 py-1.5 font-semibold transition-all duration-300 flex items-center gap-1.5 ${!store.liveMode
          ? "bg-[var(--color-night-2)] text-[var(--color-ink)] shadow-sm ring-1 ring-[var(--color-line)]"
          : "text-[var(--color-ink-faint)] hover:text-[var(--color-ink-dim)] hover:bg-[var(--color-night-2)]/50"
          }`}
        title="The stored TMYx weather year for this site, zero network. Simulations still run live against it. This is the demo path."
      >
        <span className={!store.liveMode ? "text-[var(--color-solar)]" : ""}>●</span>
        Stored Weather
      </button>
      <button
        type="button"
        onClick={() => store.setLiveMode(true)}
        title="Current conditions at the site, for context. The simulation always runs against the stored weather year, so results stay reproducible and work with the network unplugged."
        className={`rounded-md px-3 py-1.5 transition-colors font-semibold ${store.liveMode
          ? "bg-[var(--color-cold)]/10 text-[var(--color-cold)] shadow-sm"
          : "text-[var(--color-ink-faint)] hover:bg-[var(--color-night-2)]"
          }`}
      >
        ○ Live conditions{reading ? ` · ${reading}` : ""}
      </button>
    </div>
  );
}

export function Header({
  meta,
  siteId,
  candidateOptions,
  referenceOptions,
}: {
  meta: MetaResponse | undefined;
  siteId: string | null;
  candidateOptions: string[];
  referenceOptions: string[];
}) {
  const store = useStore();
  const owned = ["Site Input", "Simulation"].includes(store.step);

  return (
    <header className="flex-shrink-0 h-16 border-b border-[var(--color-line)] bg-white px-6 flex items-center justify-between z-10">
      <div className="flex items-center gap-4 flex-1">
         {/* Could put breadcrumbs or step title here */}
         <h2 className="display text-lg font-bold tracking-tight text-[var(--color-ink)] capitalize">{store.step}</h2>
      </div>

      <div className="no-print ml-auto flex items-center gap-4">
        {!owned && (
          <select
            value={siteId ?? ""}
            onChange={(event) => store.setSiteId(event.target.value)}
            className="px-3 py-1.5 text-[13px] font-semibold bg-white border border-[var(--color-line)] hover:border-[var(--color-ink-faint)] transition-all rounded-lg focus:ring-2 focus:ring-[var(--color-solar)] focus:outline-none cursor-pointer shadow-sm"
          >
            {meta?.sites.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        )}
        {!owned && (
          <select
            value={store.designName}
            onChange={(event) => store.setDesignName(event.target.value)}
            className="px-3 py-1.5 text-[13px] font-semibold bg-white border border-[var(--color-line)] hover:border-[var(--color-ink-faint)] transition-all rounded-lg focus:ring-2 focus:ring-[var(--color-solar)] focus:outline-none cursor-pointer shadow-sm"
          >
            <optgroup label="Candidates">
              {candidateOptions.map((name) => (
                <option key={name} value={name}>
                  {name.replace(/_/g, " ")}
                </option>
              ))}
            </optgroup>
            <optgroup label="References, for comparison">
              {referenceOptions.map((name) => (
                <option key={name} value={name}>
                  {name.replace(/_/g, " ")}
                </option>
              ))}
            </optgroup>
          </select>
        )}
        <ModeToggle />
      </div>
    </header>
  );
}

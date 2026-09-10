import { useQuery } from "@tanstack/react-query";
import { api, type MetaResponse } from "../lib/api";
import { STEPS, useStore, type Step } from "../lib/store";

function Stepper({ step, onChange }: { step: Step; onChange: (step: Step) => void }) {
  const index = STEPS.indexOf(step);
  return (
    <nav className="flex flex-wrap items-center gap-1">
      {STEPS.map((item, position) => {
        const active = item === step;
        const done = position < index;
        return (
          <div key={item} className="flex items-center">
            <button
              type="button"
              onClick={() => onChange(item)}
              className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition ${
                active
                  ? "border-[var(--color-solar)] bg-[var(--color-solar)]/15 text-[var(--color-solar)] font-medium"
                  : done
                    ? "border-[var(--color-line)] text-[var(--color-ink-dim)] bg-white/50"
                    : "border-transparent text-[var(--color-ink-faint)] hover:bg-[var(--color-line-soft)]"
              }`}
            >
              <span
                className={`flex h-4 w-4 items-center justify-center rounded-full text-[10px] ${
                  active
                    ? "bg-[var(--color-solar)] text-white"
                    : done
                      ? "bg-[var(--color-line)] text-[var(--color-ink)]"
                      : "border border-[var(--color-line)]"
                }`}
              >
                {position + 1}
              </span>
              {item}
            </button>
            {position < STEPS.length - 1 && (
              <span className="mx-0.5 h-px w-3 bg-[var(--color-line-soft)]" />
            )}
          </div>
        );
      })}
    </nav>
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
    <div className="flex items-center gap-1 rounded-xl border border-[var(--color-line)] bg-white/70 p-1 text-sm shadow-sm backdrop-blur-md">
      <button
        type="button"
        onClick={() => store.setLiveMode(false)}
        className={`rounded-lg px-3 py-1.5 transition-colors ${
          !store.liveMode
            ? "bg-[var(--color-good)]/15 text-[var(--color-good)] font-medium shadow-sm"
            : "text-[var(--color-ink-faint)] hover:bg-white"
        }`}
        title="The stored TMYx weather year for this site, zero network. Simulations still run live against it. This is the demo path."
      >
        ● Weather: stored file
      </button>
      <button
        type="button"
        onClick={() => store.setLiveMode(true)}
        title="Current conditions at the site, for context. The simulation always runs against the stored weather year, so results stay reproducible and work with the network unplugged."
        className={`rounded-lg px-3 py-1.5 transition-colors ${
          store.liveMode
            ? "bg-[var(--color-cold)]/15 font-medium text-[var(--color-cold)] shadow-sm"
            : "text-[var(--color-ink-faint)] hover:bg-white"
        }`}
      >
        ○ Live conditions{reading ? ` · ${reading}` : ""}
      </button>
    </div>
  );
}

export function Navbar({
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
  const owned = store.step === "Design";

  return (
    <header className="sticky top-0 z-20 border-b border-white/20 bg-white/60 backdrop-blur-xl shadow-sm">
      <div className="mx-auto flex max-w-[1360px] flex-wrap items-center gap-4 px-5 py-3">
        <div className="mr-2">
          <h1 className="display text-lg font-semibold tracking-tight text-[var(--color-ink)] leading-none mb-1">
            Entropy Zero
          </h1>
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-ink-faint)] font-medium">
            DRDO · thermal comfort · Ladakh
          </p>
        </div>

        <div className="no-print ml-auto flex flex-wrap items-center gap-4">
          {!owned && (
          <select
            value={siteId ?? ""}
            onChange={(event) => store.setSiteId(event.target.value)}
            className="px-4 py-2 text-sm shadow-sm bg-white/90 border border-[var(--color-line)] hover:border-[var(--color-solar)] hover:shadow-md transition-all rounded-xl focus:ring-2 focus:ring-[var(--color-solar)] outline-none"
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
            className="px-4 py-2 text-sm shadow-sm bg-white/90 border border-[var(--color-line)] hover:border-[var(--color-solar)] hover:shadow-md transition-all rounded-xl focus:ring-2 focus:ring-[var(--color-solar)] outline-none"
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
      </div>
      <div className="no-print mx-auto max-w-[1360px] overflow-x-auto px-5 pb-3">
        <Stepper step={store.step} onChange={store.setStep} />
      </div>
    </header>
  );
}

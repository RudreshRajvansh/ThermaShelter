import { useQuery } from "@tanstack/react-query";
import { api, type MetaResponse } from "../lib/api";
import { STEPS, useStore, type Step } from "../lib/store";
import { motion } from "framer-motion";
import { Map, Wind, Settings2, Play, BarChart2, Split, FileText, Check } from "lucide-react";

const STEP_ICONS: Record<Step, React.ElementType> = {
  "Site Input": Map,
  "Climate Data": Wind,
  "Design Parameters": Settings2,
  "Simulation": Play,
  "Results": BarChart2,
  "Compare Designs": Split,
  "Reports": FileText,
};

function Stepper({ step, onChange }: { step: Step; onChange: (step: Step) => void }) {
  const index = STEPS.indexOf(step);
  return (
    <nav className="flex items-center gap-1">
      {STEPS.map((item, position) => {
        const active = item === step;
        const done = position < index;
        const Icon = STEP_ICONS[item];
        return (
          <div key={item} className="flex items-center">
            <button
              type="button"
              onClick={() => onChange(item)}
              className={`relative flex items-center gap-2 rounded-full px-4 py-2 text-xs font-medium transition-all duration-300 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-[var(--color-solar)] focus:outline-none ${
                active
                  ? "text-white"
                  : done
                    ? "text-[var(--color-ink-dim)] hover:text-[var(--color-ink)]"
                    : "text-[var(--color-ink-faint)] hover:text-[var(--color-ink-dim)]"
              }`}
            >
              {active && (
                <motion.div
                  layoutId="activeStep"
                  className="absolute inset-0 rounded-full bg-[var(--color-solar)] shadow-md"
                  transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
                />
              )}
              <span className="relative z-10 flex items-center justify-center">
                {done ? (
                  <Check className="w-4 h-4 text-[var(--color-solar)]" />
                ) : (
                  <Icon className={`w-4 h-4 ${active ? "text-white" : ""}`} />
                )}
              </span>
              <span className="relative z-10">{item}</span>
            </button>
            {position < STEPS.length - 1 && (
              <span className="mx-1 h-px w-4 bg-gradient-to-r from-[var(--color-line)] to-[var(--color-line)]/50" />
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
    <div className="flex items-center gap-1 rounded-xl border border-[var(--color-line)] bg-[var(--color-night-2)]/80 p-1 text-xs shadow-sm backdrop-blur-md">
      <button
        type="button"
        onClick={() => store.setLiveMode(false)}
        className={`rounded-lg px-3 py-1.5 font-medium transition-all duration-300 flex items-center gap-1.5 ${
          !store.liveMode
            ? "bg-white text-[var(--color-ink)] shadow-sm ring-1 ring-[var(--color-line)]"
            : "text-[var(--color-ink-faint)] hover:text-[var(--color-ink-dim)] hover:bg-white/50"
        }`}
        title="The stored TMYx weather year for this site, zero network. Simulations still run live against it. This is the demo path."
      >
        <span className={!store.liveMode ? "text-[var(--color-good)]" : ""}>●</span> 
        Stored Weather
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
    <header className="sticky top-0 z-50 border-b border-[var(--color-line)] bg-white/70 backdrop-blur-xl transition-all">
      <div className="mx-auto flex max-w-[1360px] flex-wrap items-center gap-6 px-4 py-4 sm:px-8">
        <div>
          <h1 className="display text-xl font-bold tracking-tight text-[var(--color-ink)] leading-none flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[var(--color-solar)] to-[var(--color-cold)] shadow-inner flex items-center justify-center">
               <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                 <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
               </svg>
            </div>
            Entropy Zero
          </h1>
        </div>

        <div className="no-print ml-auto flex flex-wrap items-center gap-4">
          {!owned && (
          <select
            value={siteId ?? ""}
            onChange={(event) => store.setSiteId(event.target.value)}
            className="px-3 py-1.5 text-[13px] font-medium bg-[var(--color-night-2)] border border-[var(--color-line)] hover:border-[var(--color-ink-faint)] transition-all rounded-lg focus:ring-2 focus:ring-[var(--color-solar)] focus:bg-white outline-none cursor-pointer"
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
            className="px-3 py-1.5 text-[13px] font-medium bg-[var(--color-night-2)] border border-[var(--color-line)] hover:border-[var(--color-ink-faint)] transition-all rounded-lg focus:ring-2 focus:ring-[var(--color-solar)] focus:bg-white outline-none cursor-pointer"
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
      <div className="no-print mx-auto max-w-[1360px] overflow-x-auto px-4 sm:px-8 pb-3 scrollbar-hide">
        <Stepper step={store.step} onChange={store.setStep} />
      </div>
    </header>
  );
}

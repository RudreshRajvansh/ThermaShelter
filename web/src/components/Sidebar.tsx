import { STEPS, useStore } from "../lib/store";
import { FileText, Activity, List, CloudRain, Layers, BarChart, Split } from "lucide-react";
export function Sidebar() {
  const store = useStore();

  const stepIcon: Record<typeof STEPS[number], React.ElementType> = {
    "Site Input": List,
    "Climate Data": CloudRain,
    "Design Parameters": Layers,
    "Simulation": Activity,
    "Results": BarChart,
    "Compare Designs": Split,
    "Reports": FileText,
  };

  return (
    <aside className="w-64 flex-shrink-0 border-r border-[var(--color-line)] bg-[var(--color-night-2)] flex flex-col h-full z-50">
      {/* Logo Area */}
      <div className="h-16 flex items-center px-6 border-b border-[var(--color-line)] gap-3 bg-white">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[var(--color-solar)] to-indigo-500 shadow-sm flex items-center justify-center text-white">
           <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
           </svg>
        </div>
        <span className="display text-lg font-bold tracking-tight text-[var(--color-ink)]">Entropy Zero</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-6 space-y-6">
        <div className="text-xs font-bold tracking-wider text-[var(--color-ink-faint)] uppercase mb-2">Workflow</div>
        <div className="relative border-l-2 border-[var(--color-line)] ml-3 pl-6 space-y-8">
          {STEPS.map((step, index) => {
            const Icon = stepIcon[step];
            const isActiveStep = store.step === step;
            const isPastStep = STEPS.indexOf(store.step) > index;
            
            return (
              <div key={step} className="relative">
                {/* Timeline node */}
                <div className={`absolute -left-[35px] top-1.5 w-4 h-4 rounded-full border-2 ${
                  isActiveStep 
                    ? "bg-white border-[var(--color-solar)] ring-4 ring-[var(--color-solar)]/20" 
                    : isPastStep 
                      ? "bg-[var(--color-solar)] border-[var(--color-solar)]" 
                      : "bg-[var(--color-night-2)] border-[var(--color-line)]"
                }`} />

                <button
                  onClick={() => store.setStep(step)}
                  className={`w-full flex items-center gap-3 py-1 font-bold transition-all duration-200 text-left ${
                    isActiveStep 
                      ? "text-[var(--color-ink)]" 
                      : isPastStep
                        ? "text-[var(--color-ink-dim)] hover:text-[var(--color-ink)]"
                        : "text-[var(--color-ink-faint)] hover:text-[var(--color-ink-dim)]"
                  }`}
                >
                  <Icon className={`w-5 h-5 ${isActiveStep ? "text-[var(--color-solar)]" : isPastStep ? "text-[var(--color-solar)]" : ""}`} />
                  <span className="text-[15px]">{step}</span>
                </button>
              </div>
            );
          })}
        </div>
      </nav>
    </aside>
  );
}

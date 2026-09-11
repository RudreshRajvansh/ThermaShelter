import type { ReactNode } from "react";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { Assumption, EngineWarning } from "../lib/api";
import { bySeverity, num } from "../lib/format";

export function Panel({
  title,
  subtitle,
  right,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel p-6 sm:p-8 ${className}`}>
      {(title || right) && (
        <header className="mb-6 flex items-end justify-between gap-4 border-b border-gray-100 pb-4">
          <div>
            {title && <h2 className="text-xl font-bold tracking-tight text-gray-900">{title}</h2>}
            {subtitle && (
              <p className="mt-2 max-w-2xl text-[13px] font-medium leading-relaxed text-gray-500">
                {subtitle}
              </p>
            )}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span className="block text-[11px] font-bold text-gray-700 uppercase tracking-widest">
          {label}
        </span>
        {hint && (
          <span className="block text-[10px] text-gray-400 font-medium truncate max-w-[60%]" title={hint}>
            {hint}
          </span>
        )}
      </div>
      {children}
    </label>
  );
}

export function Accordion({
  title,
  subtitle,
  children,
  defaultOpen = false,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="panel-flat">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={`flex w-full items-center justify-between gap-3 px-5 py-4 text-left hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-[var(--color-solar)] focus:outline-none transition-all ${open ? "rounded-t-2xl" : "rounded-2xl"}`}
      >
        <span>
          <span className="text-[15px] font-semibold tracking-tight text-slate-800">{title}</span>
          {subtitle && (
            <span className="ml-3 text-xs font-medium text-slate-500">{subtitle}</span>
          )}
        </span>
        <span className={`text-slate-400 transition-transform duration-300 ${open ? "rotate-180" : ""}`}>
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: "easeInOut" }}
            className="overflow-hidden"
          >
            <div className="border-t border-slate-100/60 p-5 pt-4 bg-white/50 rounded-b-2xl">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: readonly T[];
  active: T;
  onChange: (tab: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5 p-1 bg-slate-200/50 rounded-xl w-max shadow-inner relative">
      {tabs.map((tab) => (
        <button
          key={tab}
          type="button"
          onClick={() => onChange(tab)}
          className={`relative z-10 px-4 py-1.5 text-xs capitalize rounded-lg transition-all duration-300 focus-visible:ring-2 focus-visible:ring-[var(--color-solar)] focus:outline-none ${active === tab
              ? "text-[var(--color-solar)] font-semibold"
              : "text-[var(--color-ink-dim)] hover:text-[var(--color-ink)] hover:bg-slate-200/50"
            }`}
        >
          {active === tab && (
            <motion.div
              layoutId="activeTab"
              className="absolute inset-0 bg-white rounded-lg shadow-sm"
              style={{ zIndex: -1 }}
              transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
            />
          )}
          {tab}
        </button>
      ))}
    </div>
  );
}

export function Stat({
  label,
  value,
  unit,
  baseline,
  baselineLabel,
  band,
  tone = "neutral",
  note,
  large,
}: {
  label: string;
  value: string;
  unit?: string;
  baseline?: string;
  baselineLabel?: string;
  band?: string;
  tone?: "neutral" | "good" | "bad" | "solar";
  note?: string;
  large?: boolean;
}) {
  const accent = {
    neutral: "border-transparent bg-[#f9fafb]",
    good: "border-emerald-200 bg-emerald-50",
    bad: "border-rose-200 bg-rose-50",
    solar: "border-[var(--color-solar)] bg-emerald-50/50",
  }[tone];
  return (
    <div className={`rounded-xl border ${accent} p-4 transition-all hover:shadow-sm`}>
      <div className="text-[10px] font-bold uppercase tracking-widest text-slate-500">
        {label}
      </div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span
          className={`tnum font-bold tracking-tight ${large ? "text-4xl" : "text-2xl"} ${tone === "solar" ? "text-[var(--color-solar)]" : "text-slate-900"
            }`}
        >
          {value}
        </span>
        {unit && <span className="text-xs font-semibold text-slate-400">{unit}</span>}
      </div>
      {band && <div className="mt-2 text-[11px] font-medium text-slate-400">{band}</div>}
      {baseline && (
        <div className="mt-3 flex items-center gap-1.5 border-t border-slate-200/60 pt-3 text-[11px] font-semibold">
          <span className="text-rose-500 bg-rose-50 px-1.5 py-0.5 rounded-md">{baseline}</span>
          <span className="text-slate-500">
            {baselineLabel ?? "current construction"}
          </span>
        </div>
      )}
      {note && (
        <div className="mt-2 text-[10px] uppercase tracking-wider font-semibold text-slate-400">
          {note}
        </div>
      )}
    </div>
  );
}

const SEVERITY_STYLE: Record<string, string> = {
  fatal: "border-[var(--color-diesel)] bg-[var(--color-diesel)]/10 text-red-700",
  severe: "border-[var(--color-diesel)] bg-[var(--color-diesel)]/10 text-red-700",
  warning: "border-amber-500/60 bg-amber-500/10 text-amber-800",
  info: "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-dim)]",
};

export function Warnings({
  warnings,
  collapsedInfo = true,
}: {
  warnings: EngineWarning[];
  collapsedInfo?: boolean;
}) {
  const [showInfo, setShowInfo] = useState(!collapsedInfo);
  if (warnings.length === 0) return null;
  const sorted = bySeverity(warnings);
  const info = sorted.filter((item) => item.severity === "info");
  const shown = showInfo ? sorted : sorted.filter((item) => item.severity !== "info");
  return (
    <div className="space-y-1.5">
      {shown.map((item, index) => (
        <div
          key={`${item.code}-${index}`}
          className={`rounded-md border px-3 py-2 text-sm leading-relaxed transition-all duration-300 ${SEVERITY_STYLE[item.severity] ?? SEVERITY_STYLE.info
            }`}
        >
          <span className="font-mono text-xs uppercase tracking-wide opacity-80 font-medium">
            {item.severity} · {item.code}
            {item.count > 1 ? ` ×${item.count}` : ""}
          </span>
          <p className="mt-1">{item.message}</p>
        </div>
      ))}
      {info.length > 0 && (
        <button
          type="button"
          onClick={() => setShowInfo((value) => !value)}
          className="text-xs text-[var(--color-ink-dim)] hover:text-[var(--color-ink)] underline focus-visible:ring-2 focus-visible:ring-[var(--color-solar)] focus:outline-none rounded-sm transition-colors"
        >
          {showInfo ? "hide" : "show"} {info.length} informational messages
        </button>
      )}
    </div>
  );
}

export function AssumptionTable({ assumptions }: { assumptions: Assumption[] }) {
  const [all, setAll] = useState(false);
  const rows = all ? assumptions : assumptions.slice(0, 14);
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-[var(--color-ink-faint)]">
            <tr>
              <th className="py-2 pr-3 font-medium">Input</th>
              <th className="py-2 pr-3 font-medium">Value</th>
              <th className="py-2 pr-3 font-medium">Status</th>
              <th className="py-2 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item, index) => (
              <tr key={`${item.name}-${index}`} className="border-t border-[var(--color-line-soft)] hover:bg-[var(--color-night)]/50 transition-colors">
                <td className="py-2 pr-3 font-mono">{item.name}</td>
                <td className="tnum py-2 pr-3">
                  {typeof item.value === "number"
                    ? num(item.value, 3)
                    : String(item.value).slice(0, 70)}
                  {item.unit ? ` ${item.unit}` : ""}
                </td>
                <td className="py-2 pr-3">
                  <span
                    className={`rounded px-2 py-1 text-xs font-medium ${item.source.status === "verified"
                        ? "bg-[var(--color-good)]/20 text-[var(--color-good)]"
                        : "bg-amber-100 text-amber-800"
                      }`}
                  >
                    {item.source.status}
                  </span>
                </td>
                <td className="py-2 text-[var(--color-ink-dim)]">
                  {item.source.citation}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {assumptions.length > 14 && (
        <button
          type="button"
          onClick={() => setAll((value) => !value)}
          className="text-xs text-[var(--color-ink-dim)] hover:text-[var(--color-ink)] underline focus-visible:ring-2 focus-visible:ring-[var(--color-solar)] focus:outline-none rounded-sm transition-colors"
        >
          {all ? "show fewer" : `show all ${assumptions.length}`}
        </button>
      )}
    </div>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-[var(--color-ink-dim)]">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[var(--color-line)] border-t-[var(--color-solar)]" />
      {label}
    </div>
  );
}

export function Failure({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="rounded-md border border-[var(--color-diesel)] bg-[var(--color-diesel)]/10 p-3 text-sm text-red-700 font-medium transition-all duration-300">
      {message}
    </div>
  );
}

export function Empty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--color-line)] bg-[var(--color-night-2)]/60 p-4">
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-xs leading-relaxed text-[var(--color-ink-dim)]">{detail}</p>
    </div>
  );
}

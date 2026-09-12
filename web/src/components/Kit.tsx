import type { ReactNode } from "react";
import { useState } from "react";
import { ApiError, type Assumption, type EngineWarning } from "../lib/api";
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
    <section className={`panel p-4 ${className}`}>
      {(title || right) && (
        <header className="mb-3 flex items-start justify-between gap-4">
          <div>
            {title && <h2 className="text-lg font-semibold tracking-tight text-[var(--color-ink)]">{title}</h2>}
            {subtitle && (
              <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--color-ink-dim)]">
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
      <span className="mb-2 block text-[13px] font-medium text-[var(--color-ink-dim)]">
        {label}
      </span>
      {children}
      {hint && (
        <span className="mt-2 block text-[12px] leading-relaxed text-[var(--color-ink-faint)]">
          {hint}
        </span>
      )}
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
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-[var(--color-line-soft)]/50 transition-colors rounded-t-lg"
      >
        <span>
          <span className="text-base font-medium tracking-tight">{title}</span>
          {subtitle && (
            <span className="ml-2 text-xs text-[var(--color-ink-faint)]">{subtitle}</span>
          )}
        </span>
        <span className="text-[var(--color-ink-faint)]">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="border-t border-[var(--color-line-soft)] p-3">{children}</div>}
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
    <div className="flex flex-wrap gap-1 border-b border-[var(--color-line)]">
      {tabs.map((tab) => (
        <button
          key={tab}
          type="button"
          onClick={() => onChange(tab)}
          className={`-mb-px border-b-2 px-3 py-1.5 text-xs capitalize transition ${
            active === tab
              ? "border-[var(--color-solar)] font-medium text-[var(--color-ink)]"
              : "border-transparent text-[var(--color-ink-faint)] hover:text-[var(--color-ink-dim)]"
          }`}
        >
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
    neutral: "border-[var(--color-line)]",
    good: "border-[var(--color-good)]/60",
    bad: "border-[var(--color-diesel)]/60",
    solar: "border-[var(--color-solar)]/70",
  }[tone];
  return (
    <div className={`panel-flat border ${accent} p-3`}>
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-ink-faint)]">
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span
          className={`tnum font-semibold ${large ? "text-3xl" : "text-xl"} ${
            tone === "solar" ? "text-[var(--color-solar-ink)]" : ""
          }`}
        >
          {value}
        </span>
        {unit && <span className="text-xs text-[var(--color-ink-dim)]">{unit}</span>}
      </div>
      {band && <div className="mt-1 text-[11px] text-[var(--color-ink-faint)]">{band}</div>}
      {baseline && (
        <div className="mt-1.5 border-t border-[var(--color-line-soft)] pt-1.5 text-[11px]">
          <span className="text-[var(--color-diesel)]">{baseline}</span>
          <span className="ml-1 text-[var(--color-ink-faint)]">
            {baselineLabel ?? "current construction"}
          </span>
        </div>
      )}
      {note && (
        <div className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
          {note}
        </div>
      )}
    </div>
  );
}

const SEVERITY_STYLE: Record<string, string> = {
  fatal: "border-[var(--color-diesel)] bg-[var(--color-diesel)]/10 text-[var(--color-danger-ink)]",
  severe: "border-[var(--color-diesel)] bg-[var(--color-diesel)]/10 text-[var(--color-danger-ink)]",
  warning: "border-[var(--color-solar)]/60 bg-[var(--color-solar)]/10 text-[var(--color-warn-ink)]",
  info: "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-faint)]",
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
          className={`rounded-md border px-3 py-2 text-[11px] leading-relaxed ${
            SEVERITY_STYLE[item.severity] ?? SEVERITY_STYLE.info
          }`}
        >
          <span className="font-mono text-[10px] uppercase tracking-wide opacity-75">
            {item.severity} · {item.code}
            {item.count > 1 ? ` ×${item.count}` : ""}
          </span>
          <p className="mt-0.5">{item.message}</p>
        </div>
      ))}
      {info.length > 0 && (
        <button
          type="button"
          onClick={() => setShowInfo((value) => !value)}
          className="text-[11px] text-[var(--color-ink-faint)] underline"
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
        <table className="w-full text-left text-[11px]">
          <thead className="text-[var(--color-ink-faint)]">
            <tr>
              <th className="py-1 pr-3 font-medium">Input</th>
              <th className="py-1 pr-3 font-medium">Value</th>
              <th className="py-1 pr-3 font-medium">Status</th>
              <th className="py-1 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item, index) => (
              <tr key={`${item.name}-${index}`} className="border-t border-[var(--color-line-soft)]">
                <td className="py-1.5 pr-3 font-mono">{item.name}</td>
                <td className="tnum py-1.5 pr-3">
                  {typeof item.value === "number"
                    ? num(item.value, 3)
                    : String(item.value).slice(0, 70)}
                  {item.unit ? ` ${item.unit}` : ""}
                </td>
                <td className="py-1.5 pr-3">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] ${
                      item.source.status === "verified"
                        ? "bg-[var(--color-good)]/20 text-[var(--color-good)]"
                        : "bg-[var(--color-solar)]/15 text-[var(--color-solar-ink)]"
                    }`}
                  >
                    {item.source.status}
                  </span>
                </td>
                <td className="py-1.5 text-[var(--color-ink-faint)]">
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
          className="text-[11px] text-[var(--color-ink-faint)] underline"
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
  const detail =
    error instanceof ApiError && error.path
      ? `${error.status} from ${error.path.split("?")[0]}`
      : null;
  return (
    <div className="rounded-md border border-[var(--color-diesel)] bg-[var(--color-diesel)]/10 p-3 text-sm text-[var(--color-danger-ink)]">
      <p>{message}</p>
      {detail && <p className="tnum mt-1 text-[11px] opacity-80">{detail}</p>}
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

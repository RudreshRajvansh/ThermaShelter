import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type DesignSpec,
  type DesignSummary,
  type MetaResponse,
  type ShelterUseSummary,
} from "../lib/api";
import { Accordion, Field, Loading } from "../components/Kit";
import { LocationPicker } from "../components/LocationPicker";
import { LEVER_NAMES } from "../components/SearchRunner";
import { BUDGETS, activeDesign, useStore } from "../lib/store";
import { num } from "../lib/format";

function set<T extends object>(base: T, patch: Partial<T>): T {
  return { ...base, ...patch };
}

const FACADE_ORDER = ["south", "east", "west", "north"];
const POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

function compassPoint(bearing: number): string {
  const wrapped = ((bearing % 360) + 360) % 360;
  return POINTS[Math.round(wrapped / 45) % 8];
}

function Section({
  step,
  title,
  subtitle,
  children,
}: {
  step: number;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="panel space-y-5 p-5 sm:p-6">
      <header className="flex items-start gap-3">
        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-solar)] text-[13px] font-semibold text-white">
          {step}
        </span>
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-[var(--color-ink)]">{title}</h2>
          {subtitle && (
            <p className="mt-1 max-w-3xl text-sm leading-relaxed text-[var(--color-ink-dim)]">
              {subtitle}
            </p>
          )}
        </div>
      </header>
      {children}
    </section>
  );
}

function Compass({ bearing }: { bearing: number }) {
  const angle = (bearing * Math.PI) / 180;
  const x = 28 + 20 * Math.sin(angle);
  const y = 28 - 20 * Math.cos(angle);
  return (
    <svg viewBox="0 0 56 56" width={56} height={56} aria-hidden="true">
      <circle cx={28} cy={28} r={24} fill="#f8fafc" stroke="var(--color-line)" />
      <text x={28} y={9} textAnchor="middle" fontSize={8} fill="#64748b">N</text>
      <line x1={28} y1={28} x2={x} y2={y} stroke="var(--color-solar)" strokeWidth={3} strokeLinecap="round" />
      <circle cx={28} cy={28} r={2.5} fill="var(--color-ink)" />
    </svg>
  );
}

export function InputPage({
  meta,
  catalogue,
  summaries,
}: {
  meta: MetaResponse | undefined;
  catalogue: Record<string, DesignSpec> | undefined;
  summaries: DesignSummary[] | undefined;
}) {
  const store = useStore();
  const design = activeDesign(store);
  const [budget, setBudget] = useState(BUDGETS[0]);

  const uses = useQuery({ queryKey: ["shelter-uses"], queryFn: api.shelterUses, staleTime: Infinity });
  const glazing = useQuery({ queryKey: ["glazing"], queryFn: api.glazing, staleTime: Infinity });

  const candidates = useMemo(
    () => (summaries ?? []).filter((item) => !item.is_reference && catalogue?.[item.id]),
    [summaries, catalogue],
  );

  if (!meta || !design) return <Loading label="loading the design library…" />;

  const site = meta.sites.find((item) => item.id === store.siteId);
  const running = Boolean(store.search.jobId);
  const update = (patch: Partial<DesignSpec>) => store.applyOverride(set(design, patch));
  const programme: ShelterUseSummary | undefined = (uses.data ?? []).find(
    (item) => item.use === design.plan.use,
  );

  const applyUse = (value: string) => {
    const match =
      candidates.find((item) => item.use === value && item.arrangement === design.arrangement) ??
      candidates.find((item) => item.use === value);
    if (match) {
      store.setDesignName(match.id);
      return;
    }
    const chosen = (uses.data ?? []).find((item) => item.use === value);
    if (!chosen) return;
    update({
      plan: set(design.plan, {
        use: chosen.plan.use,
        floor_area_m2: chosen.plan.floor_area_m2,
        aspect_ratio: chosen.plan.aspect_ratio,
        ceiling_height_m: chosen.plan.ceiling_height_m,
      }),
      occupancy: chosen.occupancy,
    });
  };

  const applyOccupants = (people: number) => {
    const count = Number.isFinite(people) ? Math.max(0, Math.min(200, Math.round(people))) : 0;
    if (!programme) {
      update({ occupancy: set(design.occupancy, { occupants: count }) });
      return;
    }
    const area = Math.max(4, programme.area_per_person_m2 * Math.max(count, 1));
    update({
      plan: set(design.plan, { floor_area_m2: Number(area.toFixed(1)) }),
      occupancy: set(design.occupancy, {
        occupants: count,
        equipment_w: Number(
          ((programme.occupancy.equipment_w / Math.max(programme.default_occupants, 1)) * count).toFixed(1),
        ),
        lighting_w: Number(
          ((programme.occupancy.lighting_w / Math.max(programme.default_floor_area_m2, 1)) * area).toFixed(1),
        ),
        additional_moisture_g_h: Number(
          ((programme.occupancy.additional_moisture_g_h / Math.max(programme.default_occupants, 1)) * count).toFixed(1),
        ),
        unoccupied: count === 0,
      }),
    });
  };

  const arrangements = Array.from(new Set(candidates.map((item) => item.arrangement)));
  const chooseArrangement = (value: string) => {
    const sameUse = candidates.find(
      (item) => item.use === design.plan.use && item.arrangement === value,
    );
    if (sameUse) {
      store.setDesignName(sameUse.id);
      return;
    }
    const generic = candidates.find((item) => item.arrangement === value);
    const base = generic ? catalogue?.[generic.id] : undefined;
    if (!generic || !base) return;
    store.setDesignName(generic.id);
    store.applyOverride({ ...base, plan: design.plan, occupancy: design.occupancy });
  };

  const facades = Object.keys(design.windows ?? {}).sort(
    (a, b) => FACADE_ORDER.indexOf(a) - FACADE_ORDER.indexOf(b),
  );
  const setWindow = (facade: string, patch: Record<string, unknown>) => {
    const windows = design.windows ?? {};
    const current = windows[facade as keyof typeof windows];
    if (!current) return;
    update({
      windows: { ...windows, [facade]: { ...current, ...patch } } as DesignSpec["windows"],
    });
  };

  const input = "w-full px-3 py-2 text-sm";

  return (
    <div className="mx-auto max-w-[1180px] space-y-5">
      <div className="px-1">
        <h1 className="text-2xl font-semibold tracking-tight text-[var(--color-ink)]">
          Design a passive shelter
        </h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-ink-dim)]">
          Choose the exact spot, say what the shelter is for, and set its shape. The next page
          simulates it hour by hour in EnergyPlus against that spot's weather and terrain, and can
          search for the best version. Nothing on this page is a result.
        </p>
      </div>

      {running && (
        <p className="rounded-lg border border-[var(--color-solar)]/40 bg-[var(--color-solar)]/10 px-4 py-2 text-sm text-[var(--color-ink-dim)]">
          A search is still running for the inputs you submitted. Changes here apply to the next
          analysis or search.
        </p>
      )}

      <Section
        step={1}
        title="Where it stands"
        subtitle="Precise to about a metre. The site's elevation, terrain horizon, ground and weather all follow from the point you choose."
      >
        <LocationPicker
          meta={meta}
          siteId={store.siteId}
          onSelect={store.setSiteId}
          disabled={running}
        />
      </Section>

      <div className="grid gap-5 lg:grid-cols-2">
        <Section step={2} title="What it is for">
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Use">
              <select
                value={design.plan.use}
                onChange={(event) => applyUse(event.target.value)}
                disabled={!uses.data}
                className={input}
              >
                {(uses.data ?? []).map((item) => (
                  <option key={item.use} value={item.use}>
                    {item.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="People"
              hint={programme ? `The programme figure is ${programme.default_occupants}.` : undefined}
            >
              <input
                type="number"
                min={0}
                max={200}
                value={design.occupancy.occupants}
                onChange={(event) => applyOccupants(Number(event.target.value))}
                className={input}
              />
            </Field>
          </div>
          {programme && (
            <p className="text-[13px] leading-relaxed text-[var(--color-ink-faint)]">
              {programme.description} {programme.area_per_person_m2} m² per person,{" "}
              {programme.room_names.length} rooms: {programme.room_names.join(", ")}.
            </p>
          )}
        </Section>

        <Section step={3} title="Shape and openings">
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Faces" hint={`The main glazed face points ${compassPoint(design.plan.azimuth_deg)}. 180° is due south.`}>
              <div className="flex items-center gap-3">
                <Compass bearing={design.plan.azimuth_deg} />
                <input
                  type="number"
                  min={0}
                  max={359}
                  value={design.plan.azimuth_deg}
                  onChange={(event) =>
                    update({ plan: set(design.plan, { azimuth_deg: Number(event.target.value) }) })
                  }
                  className={input}
                />
              </div>
            </Field>
            <Field label="Floor area" hint="square metres">
              <input
                type="number"
                min={4}
                max={2000}
                value={design.plan.floor_area_m2}
                onChange={(event) =>
                  update({ plan: set(design.plan, { floor_area_m2: Number(event.target.value) }) })
                }
                className={input}
              />
            </Field>
            <Field label="Plan proportion" hint="width to depth">
              <input
                type="number"
                min={0.25}
                max={6}
                step={0.1}
                value={design.plan.aspect_ratio}
                onChange={(event) =>
                  update({ plan: set(design.plan, { aspect_ratio: Number(event.target.value) }) })
                }
                className={input}
              />
            </Field>
            <Field label="Ceiling height" hint="metres">
              <input
                type="number"
                min={1.8}
                max={6}
                step={0.1}
                value={design.plan.ceiling_height_m}
                onChange={(event) =>
                  update({ plan: set(design.plan, { ceiling_height_m: Number(event.target.value) }) })
                }
                className={input}
              />
            </Field>
            <Field label="Arrangement" hint="a sunspace or Trombe wall adds a second heated zone">
              <select
                value={design.arrangement}
                onChange={(event) => chooseArrangement(event.target.value)}
                className={input}
              >
                {(arrangements.includes(design.arrangement)
                  ? arrangements
                  : [design.arrangement, ...arrangements]
                ).map((item) => (
                  <option key={item} value={item}>
                    {item.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          {facades.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px]">
                <thead className="text-[12px] text-[var(--color-ink-faint)]">
                  <tr>
                    <th className="py-1.5 pr-3 font-medium">Window</th>
                    <th className="py-1.5 pr-3 font-medium">Share of wall</th>
                    <th className="py-1.5 pr-3 font-medium">Glazing</th>
                    <th className="py-1.5 font-medium">Night shutter</th>
                  </tr>
                </thead>
                <tbody>
                  {facades.map((facade) => {
                    const window = design.windows?.[facade as keyof typeof design.windows];
                    if (!window) return null;
                    return (
                      <tr key={facade} className="border-t border-[var(--color-line-soft)]">
                        <td className="py-2 pr-3 capitalize">{facade}</td>
                        <td className="py-2 pr-3">
                          <input
                            type="number"
                            min={0}
                            max={0.9}
                            step={0.01}
                            value={window.wwr}
                            onChange={(event) => setWindow(facade, { wwr: Number(event.target.value) })}
                            className="tnum w-20 px-2 py-1 text-sm"
                          />
                        </td>
                        <td className="py-2 pr-3">
                          <select
                            value={window.glazing_id}
                            onChange={(event) => setWindow(facade, { glazing_id: event.target.value })}
                            className="w-full min-w-[10rem] px-2 py-1 text-sm"
                          >
                            {(glazing.data ?? [{ id: window.glazing_id, name: window.glazing_id, u_value_w_m2k: null }]).map(
                              (item) => (
                                <option key={item.id} value={item.id}>
                                  {item.name}
                                  {typeof item.u_value_w_m2k === "number" ? ` · U ${num(item.u_value_w_m2k, 1)}` : ""}
                                </option>
                              ),
                            )}
                          </select>
                        </td>
                        <td className="py-2">
                          <input
                            type="checkbox"
                            checked={Boolean(window.shutter?.present)}
                            onChange={(event) =>
                              setWindow(facade, {
                                shutter: { ...(window.shutter ?? {}), present: event.target.checked },
                              })
                            }
                            aria-label={`Night shutter on the ${facade} window`}
                            className="h-4 w-4 accent-[var(--color-solar)]"
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </div>

      <Accordion title="Advanced" subtitle="start design, air leakage, fresh air and comfort target">
        <div className="grid gap-5 p-2 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Start from a stored design">
            <select
              value={store.designName}
              onChange={(event) => store.setDesignName(event.target.value)}
              className={input}
            >
              {candidates.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Air leakage" hint="air changes per hour">
            <input
              type="number"
              min={0.05}
              max={15}
              step={0.05}
              value={design.ventilation.infiltration_ach}
              onChange={(event) =>
                update({
                  ventilation: set(design.ventilation, {
                    infiltration_ach: Number(event.target.value),
                    infiltration_range: null,
                  }),
                })
              }
              className={input}
            />
          </Field>
          <Field label="Minimum fresh air" hint="a health floor the search cannot go below">
            <input
              type="number"
              min={0}
              max={3}
              step={0.05}
              value={design.ventilation.minimum_fresh_air_ach}
              onChange={(event) =>
                update({
                  ventilation: set(design.ventilation, {
                    minimum_fresh_air_ach: Number(event.target.value),
                  }),
                })
              }
              className={input}
            />
          </Field>
          <Field label="Comfort target">
            <select
              value={store.comfortModelId ?? meta.comfort_model_id}
              onChange={(event) => store.setComfortModelId(event.target.value)}
              className={input}
            >
              {meta.comfort_models.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Accordion>

      <Section
        step={4}
        title="Find the best shelter"
        subtitle={`The search varies ${LEVER_NAMES}. Every candidate is a full EnergyPlus run, so the size of the search is real time.`}
      >
        <div className="grid gap-3 sm:grid-cols-3" role="radiogroup" aria-label="Search size">
          {BUDGETS.map((item) => {
            const active = item.designs === budget.designs;
            return (
              <button
                key={item.designs}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setBudget(item)}
                className={`rounded-xl border p-4 text-left transition ${
                  active
                    ? "border-[var(--color-solar)] bg-[var(--color-solar)]/8 shadow-sm"
                    : "border-[var(--color-line)] bg-white hover:border-[var(--color-solar)]/60"
                }`}
              >
                <span className="block text-sm font-semibold">{item.label}</span>
                <span className="tnum mt-1 block text-[12px] text-[var(--color-ink-faint)]">
                  {item.designs} designs · about {item.minutes} min
                </span>
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-[var(--color-line-soft)] pt-5">
          <button
            type="button"
            disabled={!site || running}
            onClick={() => store.requestSearch(budget)}
            className="rounded-lg bg-[var(--color-solar)] px-6 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:shadow-md disabled:opacity-40"
          >
            Find the best shelter
          </button>
          <button
            type="button"
            disabled={!site}
            onClick={() => store.open("3d model")}
            className="rounded-lg border border-[var(--color-line)] bg-white px-5 py-2.5 text-sm text-[var(--color-ink)] transition hover:border-[var(--color-solar)] disabled:opacity-40"
          >
            Analyse this design without a search
          </button>
          {!site && (
            <span className="text-[13px] text-[var(--color-ink-faint)]">Choose a site first.</span>
          )}
        </div>
      </Section>
    </div>
  );
}

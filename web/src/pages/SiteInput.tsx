import { useQuery } from "@tanstack/react-query";
import {
  api,
  type DesignSpec,
  type MetaResponse,
  type ShelterUseSummary,
} from "../lib/api";
import { Accordion, Field, Loading, Panel } from "../components/Kit";
import { activeDesign, useStore } from "../lib/store";
import { num } from "../lib/format";

function set<T extends object>(base: T, patch: Partial<T>): T {
  return { ...base, ...patch };
}

export function SiteInput({ meta }: { meta: MetaResponse | undefined }) {
  const store = useStore();
  const design = activeDesign(store);
  const materials = useQuery({
    queryKey: ["materials"],
    queryFn: api.materials,
    staleTime: Infinity,
  });
  const uses = useQuery({
    queryKey: ["shelter-uses"],
    queryFn: api.shelterUses,
    staleTime: Infinity,
  });
  const comfortModels = useQuery({
    queryKey: ["comfort-models"],
    queryFn: api.comfortModels,
    staleTime: Infinity,
  });

  if (!design || !meta) return <Loading label="loading the design library…" />;

  const site = meta.sites.find((item) => item.id === store.siteId);
  const update = (patch: Partial<DesignSpec>) =>
    store.applyOverride(set(design, patch));

  const programme: ShelterUseSummary | undefined = (uses.data ?? []).find(
    (item) => item.use === design.plan.use,
  );

  const applyUse = (value: string) => {
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
    const count = Number.isFinite(people) ? Math.max(0, Math.round(people)) : 0;
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
          (programme.occupancy.equipment_w / Math.max(programme.default_occupants, 1) * count).toFixed(1),
        ),
        lighting_w: Number(
          (programme.occupancy.lighting_w / Math.max(programme.default_floor_area_m2, 1) * area).toFixed(1),
        ),
        additional_moisture_g_h: Number(
          (programme.occupancy.additional_moisture_g_h / Math.max(programme.default_occupants, 1) * count).toFixed(1),
        ),
        unoccupied: count === 0,
      }),
    });
  };

  const chosenComfort = store.comfortModelId ?? meta.comfort_model_id;
  const comfortBand = (comfortModels.data ?? []).find(
    (item) => item.id === chosenComfort,
  );
  const comfortTwins = (comfortModels.data ?? []).filter(
    (item) =>
      comfortBand !== undefined &&
      item.id !== comfortBand.id &&
      item.intercept_c === comfortBand.intercept_c &&
      item.slope === comfortBand.slope &&
      item.band_half_width_k === comfortBand.band_half_width_k,
  );
  const comfortHint = comfortBand
    ? comfortTwins.length > 0
      ? `Scored against ${comfortBand.intercept_c} + ${comfortBand.slope} × running mean outdoor, ±${comfortBand.band_half_width_k} K. It carries exactly the same coefficients as ${comfortTwins.map((item) => item.id).join(", ")}, so switching between them cannot change a single number yet. One is a placeholder until its paper is read.`
      : `Scored against ${comfortBand.intercept_c} + ${comfortBand.slope} × running mean outdoor, ±${comfortBand.band_half_width_k} K. This changes the comfort verdict, not the physics: the same hourly temperatures are judged against a different band.`
    : "The adaptive band the run is judged against. Fixed setpoints are not used for a free running shelter.";

  const glazingIds = Array.from(
    new Set(Object.values(design.windows ?? {}).map((w) => w.glazing_id)),
  );

  return (
    <div className="space-y-4">
      <Panel
        title="Tier one — six fields and a button"
        subtitle="This is the demo path. The location alone resolves the weather file, latitude, altitude, terrain horizon and terrain mesh; you do not enter any of those."
      >
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <Field
            label="Location"
            hint="We pull this location's weather year, terrain skyline and elevation from the Layer 0 data set."
          >
            <select
              value={store.siteId ?? ""}
              onChange={(event) => store.setSiteId(event.target.value)}
              className="w-full px-2 py-1.5 text-sm"
            >
              {meta.sites.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="What the shelter is for"
            hint={
              programme
                ? `${programme.description} ${programme.area_per_person_m2} m² per person, ${programme.ceiling_height_m.toFixed(2)} m ceiling, ${programme.room_names.length} rooms.`
                : "Sets the floor area, ceiling height, room layout and internal heat gains."
            }
          >
            <select
              value={design.plan.use}
              onChange={(event) => applyUse(event.target.value)}
              disabled={!uses.data}
              className="w-full px-2 py-1.5 text-sm"
            >
              {(uses.data ?? []).map((item) => (
                <option key={item.use} value={item.use}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="Occupants"
            hint={
              programme
                ? `${programme.default_occupants} is the programme figure. Changing it rescales equipment, lighting and moisture.`
                : "Occupants drive metabolic, equipment and moisture gains."
            }
          >
            <input
              type="number"
              min={0}
              max={60}
              value={design.occupancy.occupants}
              onChange={(event) => applyOccupants(Number(event.target.value))}
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>

          <Field
            label="Floor area"
            hint={
              programme
                ? `square metres. The programme gives ${programme.default_floor_area_m2} m² for ${design.occupancy.occupants} people.`
                : "square metres"
            }
          >
            <input
              type="number"
              min={4}
              max={400}
              step={1}
              value={design.plan.floor_area_m2}
              onChange={(event) =>
                update({
                  plan: set(design.plan, { floor_area_m2: Number(event.target.value) }),
                })
              }
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>

          <Field
            label="Budget level"
            hint="Sets which glazing and insulation the search is allowed to reach for."
          >
            <select
              value={glazingIds[0] ?? ""}
              onChange={(event) => {
                const glazing = event.target.value;
                const windows = Object.fromEntries(
                  Object.entries(design.windows ?? {}).map(([facade, window]) => [
                    facade,
                    set(window, { glazing_id: glazing }),
                  ]),
                );
                update({ windows: windows as DesignSpec["windows"] });
              }}
              className="w-full px-2 py-1.5 text-sm"
            >
              <option value="single_clear_4mm">Low — single clear glazing</option>
              <option value="double_clear_6_12_6">Medium — double clear</option>
              <option value="double_lowe_argon">High — double low-e argon</option>
            </select>
          </Field>

          <Field
            label="Comfort target"
            hint={comfortHint}
          >
            <select
              value={store.comfortModelId ?? meta.comfort_model_id}
              onChange={(event) => store.setComfortModelId(event.target.value)}
              className="w-full px-2 py-1.5 text-sm"
            >
              {meta.comfort_models.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {site && (
          <div className="mt-6 rounded-lg border border-[var(--color-line-soft)] bg-[#f8fafc] p-4 text-sm leading-relaxed text-[var(--color-ink-dim)] shadow-sm">
            <span className="text-[var(--color-ink)]">{site.name}</span> at{" "}
            {num(site.latitude_deg, 2)}° N, {num(site.longitude_deg, 2)}° E,{" "}
            {num(site.elevation_m, 0)} m. Weather file{" "}
            <span className="font-mono">{site.weather_file}</span>. Terrain horizon{" "}
            {site.has_horizon ? "attached" : "missing, a flat skyline is assumed"}, ground
            temperatures{" "}
            {site.has_ground_temperatures
              ? "attached"
              : "missing, EnergyPlus defaults will be used and they assume a temperate climate"}
            . This is a {site.climate_objective.replace(/_/g, " ")} location, so the tool
            leads with <span className="text-[var(--color-solar)]">
              {site.headline_metric.replace(/_/g, " ")}
            </span>
            .
          </div>
        )}
      </Panel>

      <Accordion
        title="Tier two — the specific site"
        subtitle="for someone who has walked the plot"
      >
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <Field
            label="Plot orientation"
            hint="Compass bearing the main glazed face points at. 180° is due south."
          >
            <input
              type="number"
              min={0}
              max={359}
              value={design.plan.azimuth_deg}
              onChange={(event) =>
                update({
                  plan: set(design.plan, { azimuth_deg: Number(event.target.value) }),
                })
              }
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>

          <Field label="Plan proportion" hint="width to depth; compact forms lose less">
            <input
              type="number"
              min={0.4}
              max={5}
              step={0.1}
              value={design.plan.aspect_ratio}
              onChange={(event) =>
                update({
                  plan: set(design.plan, { aspect_ratio: Number(event.target.value) }),
                })
              }
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>

          <Field label="Arrangement" hint="a second zone is a real design lever, not a detail">
            <select
              value={design.arrangement}
              disabled
              className="w-full px-2 py-1.5 text-sm opacity-70"
            >
              <option value={design.arrangement}>
                {design.arrangement.replace(/_/g, " ")}
              </option>
            </select>
          </Field>

          <Field
            label="Shutters operated reliably"
            hint="Night shutters only work if somebody closes them. This is reported both ways."
          >
            <select
              value={String(
                Object.values(design.windows ?? {}).some((w) => w.shutter.present),
              )}
              onChange={(event) => {
                const present = event.target.value === "true";
                const windows = Object.fromEntries(
                  Object.entries(design.windows ?? {}).map(([facade, window]) => [
                    facade,
                    set(window, { shutter: set(window.shutter, { present }) }),
                  ]),
                );
                update({ windows: windows as DesignSpec["windows"] });
              }}
              className="w-full px-2 py-1.5 text-sm"
            >
              <option value="true">Yes, shutters fitted and closed nightly</option>
              <option value="false">No shutters</option>
            </select>
          </Field>

          <Field
            label="Buildable season"
            hint="Construction in Ladakh stops from roughly November to March; the ground freezes and the passes close."
          >
            <input
              type="text"
              readOnly
              value="April to October"
              className="w-full px-2 py-1.5 text-sm opacity-70"
            />
          </Field>
        </div>
      </Accordion>

      <Accordion
        title="Tier three — advanced"
        subtitle="every property editable, kept out of the way"
      >
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Infiltration" hint="air changes per hour">
            <input
              type="number"
              min={0.05}
              max={5}
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
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>
          <Field
            label="Minimum fresh air"
            hint="A hard health floor. The optimiser cannot go below it."
          >
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
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="Ceiling height" hint="metres">
            <input
              type="number"
              min={1.8}
              max={5}
              step={0.1}
              value={design.plan.ceiling_height_m}
              onChange={(event) =>
                update({
                  plan: set(design.plan, {
                    ceiling_height_m: Number(event.target.value),
                  }),
                })
              }
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
          Every material property, glazing specification and ground temperature is editable
          through the design parameters step and the Layer 0 database.{" "}
          {materials.data
            ? `${materials.data.length} materials are loaded, each carrying the source its numbers came from.`
            : ""}{" "}
          Custom weather upload is a Layer 0 responsibility and is not wired into this
          build.
        </p>
      </Accordion>

      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => store.go("Evidence", "climate")}
          className="rounded-md bg-[var(--color-solar)] px-4 py-2 text-sm font-medium text-[#0b1220]"
        >
          Use this site →
        </button>
      </div>
    </div>
  );
}

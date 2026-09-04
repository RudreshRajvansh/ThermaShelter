import { useQuery } from "@tanstack/react-query";
import { api, type DesignSpec, type Material } from "../lib/api";
import { Field, Loading, Panel, Stat } from "../components/Kit";
import { ConstructionView } from "../components/Construction";
import { activeDesign, useStore } from "../lib/store";
import { num } from "../lib/format";

function set<T extends object>(base: T, patch: Partial<T>): T {
  return { ...base, ...patch };
}

function layerSlider(
  design: DesignSpec,
  element: "wall" | "roof" | "floor",
  index: number,
  thickness: number,
): DesignSpec {
  const construction = design[element];
  const layers = construction.layers.map((layer, position) =>
    position === index ? { ...layer, thickness_m: thickness } : layer,
  );
  return { ...design, [element]: { ...construction, layers } };
}

function LayerControls({
  design,
  element,
  label,
  materials,
  onChange,
}: {
  design: DesignSpec;
  element: "wall" | "roof" | "floor";
  label: string;
  materials: Map<string, Material>;
  onChange: (design: DesignSpec) => void;
}) {
  return (
    <div className="panel-flat p-3">
      <div className="mb-2 text-sm font-medium">{label}</div>
      <div className="space-y-2.5">
        {design[element].layers.map((layer, index) => {
          const material = materials.get(layer.material_id);
          return (
            <div key={`${layer.material_id}-${index}`}>
              <div className="mb-1 flex items-center justify-between text-[11px]">
                <span className="flex items-center gap-1.5">
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-sm"
                    style={{ background: material?.appearance.colour ?? "#4a5a70" }}
                  />
                  {material?.name ?? layer.material_id}
                </span>
                <span className="tnum text-[var(--color-ink-dim)]">
                  {num(layer.thickness_m * 1000, 0)} mm
                </span>
              </div>
              <input
                type="range"
                min={1}
                max={700}
                step={1}
                value={Math.round(layer.thickness_m * 1000)}
                onChange={(event) =>
                  onChange(
                    layerSlider(design, element, index, Number(event.target.value) / 1000),
                  )
                }
                className="w-full"
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function DesignParameters() {
  const store = useStore();
  const design = activeDesign(store);
  const materials = useQuery({
    queryKey: ["materials"],
    queryFn: api.materials,
    staleTime: Infinity,
  });
  const references = useQuery({
    queryKey: ["references"],
    queryFn: api.references,
    staleTime: Infinity,
  });

  if (!design) return <Loading label="loading the design…" />;
  const index = new Map((materials.data ?? []).map((item) => [item.id, item]));
  const update = (next: DesignSpec) => store.applyOverride(next);

  const width = Math.sqrt(design.plan.floor_area_m2 * design.plan.aspect_ratio);
  const depth = Math.sqrt(design.plan.floor_area_m2 / design.plan.aspect_ratio);
  const envelope =
    2 * design.plan.floor_area_m2 +
    2 * design.plan.ceiling_height_m * (width + depth);
  const volume = design.plan.floor_area_m2 * design.plan.ceiling_height_m;

  const baseline = references.data?.find(
    (item) => item.name === "current_practice_masonry",
  );

  return (
    <div className="space-y-4">
      <Panel
        title="Shelter dimensions and orientation"
        subtitle="These drive the geometry that gets simulated. Change one and the model on the simulation step rebuilds."
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Floor area" hint="m²">
            <input
              type="number"
              min={4}
              max={400}
              value={design.plan.floor_area_m2}
              onChange={(event) =>
                update({
                  ...design,
                  plan: set(design.plan, {
                    floor_area_m2: Number(event.target.value),
                  }),
                })
              }
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="Plan proportion" hint="width ÷ depth">
            <input
              type="number"
              min={0.4}
              max={5}
              step={0.05}
              value={design.plan.aspect_ratio}
              onChange={(event) =>
                update({
                  ...design,
                  plan: set(design.plan, { aspect_ratio: Number(event.target.value) }),
                })
              }
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="Ceiling height" hint="m">
            <input
              type="number"
              min={1.8}
              max={5}
              step={0.05}
              value={design.plan.ceiling_height_m}
              onChange={(event) =>
                update({
                  ...design,
                  plan: set(design.plan, {
                    ceiling_height_m: Number(event.target.value),
                  }),
                })
              }
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="Orientation" hint="° the glazed face points, 180 is due south">
            <input
              type="number"
              min={0}
              max={359}
              value={design.plan.azimuth_deg}
              onChange={(event) =>
                update({
                  ...design,
                  plan: set(design.plan, { azimuth_deg: Number(event.target.value) }),
                })
              }
              className="w-full px-2 py-1.5 text-sm"
            />
          </Field>
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Plan" value={`${num(width, 2)} × ${num(depth, 2)}`} unit="m" />
          <Stat label="Volume" value={num(volume, 1)} unit="m³" />
          <Stat label="Envelope area" value={num(envelope, 1)} unit="m²" />
          <Stat
            label="Surface to volume"
            value={num(envelope / volume, 2)}
            unit="1/m"
            note="cold region guidance moves towards one"
            tone={envelope / volume > 1.3 ? "bad" : "good"}
          />
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-3">
        <LayerControls
          design={design}
          element="wall"
          label="Wall build-up"
          materials={index}
          onChange={update}
        />
        <LayerControls
          design={design}
          element="roof"
          label="Roof build-up"
          materials={index}
          onChange={update}
        />
        <LayerControls
          design={design}
          element="floor"
          label="Floor build-up"
          materials={index}
          onChange={update}
        />
      </div>

      <Panel
        title="Glazing and shutters"
        subtitle="Window to wall ratio per facade. In a heating climate the south face is the collector and every other face is mostly a loss."
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(design.windows ?? {}).map(([facade, window]) => (
            <div key={facade} className="panel-flat p-3">
              <div className="mb-2 text-sm font-medium capitalize">{facade}</div>
              <div className="mb-1 flex justify-between text-[11px]">
                <span className="text-[var(--color-ink-faint)]">window to wall</span>
                <span className="tnum">{num(window.wwr * 100, 0)} %</span>
              </div>
              <input
                type="range"
                min={0}
                max={70}
                value={Math.round(window.wwr * 100)}
                onChange={(event) =>
                  update({
                    ...design,
                    windows: {
                      ...design.windows,
                      [facade]: set(window, { wwr: Number(event.target.value) / 100 }),
                    } as DesignSpec["windows"],
                  })
                }
                className="w-full"
              />
              <label className="mt-2 flex items-center gap-2 text-[11px]">
                <input
                  type="checkbox"
                  checked={window.shutter.present}
                  onChange={(event) =>
                    update({
                      ...design,
                      windows: {
                        ...design.windows,
                        [facade]: set(window, {
                          shutter: set(window.shutter, {
                            present: event.target.checked,
                          }),
                        }),
                      } as DesignSpec["windows"],
                    })
                  }
                />
                night shutter
              </label>
              <div className="mt-1 font-mono text-[10px] text-[var(--color-ink-faint)]">
                {window.glazing_id}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        title="Infiltration and ventilation"
        subtitle="Ventilation is a health constraint, never a score to maximise. The optimiser cannot go below the fresh air floor."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <div className="mb-1 flex justify-between text-[11px]">
              <span className="text-[var(--color-ink-faint)]">infiltration</span>
              <span className="tnum">{num(design.ventilation.infiltration_ach, 2)} ACH</span>
            </div>
            <input
              type="range"
              min={5}
              max={300}
              value={Math.round(design.ventilation.infiltration_ach * 100)}
              onChange={(event) =>
                update({
                  ...design,
                  ventilation: set(design.ventilation, {
                    infiltration_ach: Number(event.target.value) / 100,
                    infiltration_range: null,
                  }),
                })
              }
              className="w-full"
            />
          </div>
          <Stat
            label="Fresh air floor"
            value={num(design.ventilation.minimum_fresh_air_ach, 2)}
            unit="ACH"
            note="nine people need roughly 0.35 to 0.5"
          />
          <Stat
            label="Effective air change"
            value={num(
              Math.max(
                design.ventilation.infiltration_ach,
                design.ventilation.minimum_fresh_air_ach,
              ),
              2,
            )}
            unit="ACH"
          />
        </div>
      </Panel>

      <Panel
        title="Construction detail"
        subtitle="Outside face on the left, in the layer order EnergyPlus was given. U-values are computed with ISO 6946 surface resistances."
      >
        <ConstructionView design={design} materials={materials.data} />
      </Panel>

      {baseline && (
        <Panel
          title="Against current construction"
          subtitle={`Every result is a difference against "${baseline.name.replace(/_/g, " ")}". This is that object, not a mental comparison.`}
        >
          <ConstructionView design={baseline} materials={materials.data} />
        </Panel>
      )}

      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => store.clearOverride()}
          className="rounded-md border border-[var(--color-line)] px-3 py-2 text-xs text-[var(--color-ink-dim)]"
        >
          Reset to the stored design
        </button>
        <button
          type="button"
          onClick={() => store.setStep("Simulation")}
          className="rounded-md bg-[var(--color-solar)] px-4 py-2 text-sm font-medium text-[#0b1220]"
        >
          Simulate this design →
        </button>
      </div>
    </div>
  );
}

import type { DesignSpec, Material } from "../lib/api";
import { num, rupees } from "../lib/format";

type Layer = { material_id: string; thickness_m: number };
type Construction = { name: string; layers: Layer[] };

const R_SI: Record<string, number> = { wall: 0.13, roof: 0.1, floor: 0.17 };
const R_SO = 0.04;

function uValue(
  construction: Construction,
  materials: Map<string, Material>,
  element: string,
): number | null {
  let resistance = (R_SI[element] ?? 0.13) + (element === "floor" ? 0 : R_SO);
  for (const layer of construction.layers) {
    const material = materials.get(layer.material_id);
    if (!material) return null;
    resistance += layer.thickness_m / material.conductivity_w_mk;
  }
  return resistance > 0 ? 1 / resistance : null;
}

function ConstructionStrip({
  label,
  element,
  construction,
  materials,
}: {
  label: string;
  element: string;
  construction: Construction;
  materials: Map<string, Material>;
}) {
  const total = construction.layers.reduce((sum, layer) => sum + layer.thickness_m, 0);
  const u = uValue(construction, materials, element);
  const mass = construction.layers.reduce((sum, layer) => {
    const material = materials.get(layer.material_id);
    return sum + (material ? layer.thickness_m * material.density_kg_m3 : 0);
  }, 0);
  const cost = construction.layers.reduce((sum, layer) => {
    const material = materials.get(layer.material_id);
    if (!material) return sum;
    const delivered =
      material.cost_inr_m3 *
      (material.locally_available ? 1 : material.transport_multiplier);
    return sum + layer.thickness_m * delivered;
  }, 0);

  return (
    <div className="panel-flat p-3">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <div>
          <span className="text-sm font-medium">{label}</span>
          <span className="ml-2 font-mono text-[11px] text-[var(--color-ink-faint)]">
            {construction.name}
          </span>
        </div>
        <div className="tnum text-right text-[11px] text-[var(--color-ink-dim)]">
          <div>
            U {u === null ? "—" : num(u, 2)} W/m²K · {num(total * 1000, 0)} mm
          </div>
          <div className="text-[var(--color-ink-faint)]">
            {num(mass, 0)} kg/m² · {rupees(cost)}/m²
          </div>
        </div>
      </div>

      <div className="flex h-14 overflow-hidden rounded border border-[var(--color-line)]">
        {construction.layers.map((layer, index) => {
          const material = materials.get(layer.material_id);
          const share = total > 0 ? (layer.thickness_m / total) * 100 : 0;
          return (
            <div
              key={`${layer.material_id}-${index}`}
              className="group relative flex items-center justify-center border-r border-[var(--color-night)] last:border-r-0"
              style={{
                width: `${Math.max(share, 2)}%`,
                background: material?.appearance.colour ?? "#4a5a70",
              }}
              title={`${material?.name ?? layer.material_id} · ${num(
                layer.thickness_m * 1000,
                1,
              )} mm · k=${material ? num(material.conductivity_w_mk, 3) : "?"} W/mK`}
            >
              <span className="rotate-0 text-[9px] font-medium text-[#0b1220] opacity-0 transition group-hover:opacity-100">
                {num(layer.thickness_m * 1000, 0)}
              </span>
            </div>
          );
        })}
      </div>

      <ol className="mt-2 space-y-0.5 text-[11px] text-[var(--color-ink-dim)]">
        {construction.layers.map((layer, index) => {
          const material = materials.get(layer.material_id);
          return (
            <li key={`${layer.material_id}-${index}`} className="flex items-center gap-2">
              <span
                className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
                style={{ background: material?.appearance.colour ?? "#4a5a70" }}
              />
              <span className="text-[var(--color-ink-faint)]">{index + 1}</span>
              <span className="tnum w-16">{num(layer.thickness_m * 1000, 0)} mm</span>
              <span>{material?.name ?? layer.material_id}</span>
              {material && (
                <span className="tnum ml-auto text-[var(--color-ink-faint)]">
                  k {num(material.conductivity_w_mk, 3)} · ρ{" "}
                  {num(material.density_kg_m3, 0)}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <p className="mt-1.5 text-[10px] text-[var(--color-ink-faint)]">
        Outside face on the left. This is the layer order EnergyPlus was given.
      </p>
    </div>
  );
}

export function ConstructionView({
  design,
  materials,
}: {
  design: DesignSpec;
  materials: Material[] | undefined;
}) {
  const index = new Map((materials ?? []).map((item) => [item.id, item]));
  const items: [string, string, Construction | null | undefined][] = [
    ["Wall", "wall", design.wall as Construction],
    ["Roof", "roof", design.roof as Construction],
    ["Floor", "floor", design.floor as Construction],
    ["Partition", "wall", design.partition as Construction | null],
  ];
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {items.map(([label, element, construction]) =>
        construction ? (
          <ConstructionStrip
            key={label}
            label={label}
            element={element}
            construction={construction}
            materials={index}
          />
        ) : null,
      )}
    </div>
  );
}

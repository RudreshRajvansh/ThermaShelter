import { useQuery } from "@tanstack/react-query";
import { api, type BillOfQuantities, type SimulationResult } from "../lib/api";
import { Failure, Loading, Panel, Stat } from "./Kit";
import { useStore } from "../lib/store";
import { num } from "../lib/format";

const KIND_LABEL: Record<string, string> = {
  material: "material",
  glazing: "glazing",
  shutter: "shutter",
  pcm: "phase change",
};

function csvCell(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function toCsv(bill: BillOfQuantities): string {
  const header = [
    "item_id",
    "name",
    "kind",
    "used_in",
    "area_m2",
    "volume_m3",
    "mass_kg",
    "locally_available",
    "cost_inr",
    "note",
  ];
  const rows = bill.lines.map((line) =>
    [
      line.item_id,
      line.name,
      line.kind,
      line.used_in.join(" "),
      line.area_m2.toFixed(3),
      line.volume_m3 != null ? line.volume_m3.toFixed(4) : null,
      line.mass_kg != null ? line.mass_kg.toFixed(1) : null,
      line.locally_available,
      line.cost_inr.toFixed(0),
      line.note,
    ]
      .map(csvCell)
      .join(","),
  );
  return [header.join(","), ...rows].join("\n") + "\n";
}

function download(bill: BillOfQuantities, name: string) {
  const blob = new Blob([toCsv(bill)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${name.replace(/[^a-z0-9_-]+/gi, "_")}_bill_of_quantities.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

export function Quantities({ result }: { result: SimulationResult }) {
  const store = useStore();
  const bill = useQuery({
    queryKey: ["bill-of-quantities", result.design_fingerprint, store.siteId],
    queryFn: () => api.billOfQuantities(result.design, store.siteId),
    staleTime: Infinity,
    retry: false,
  });
  const data = bill.data;
  return (
    <Panel
      title="What has to be carried up"
      subtitle="Every material in the envelope by volume and weight, from the generated geometry."
      right={
        data ? (
          <button
            type="button"
            onClick={() => download(data, result.design.name)}
            className="rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] px-3 py-1.5 text-sm"
          >
            Download CSV
          </button>
        ) : undefined
      }
    >
      {bill.error && <Failure error={bill.error} />}
      {!data && !bill.error && <Loading label="weighing the envelope…" />}
      {data && (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat
              label="Envelope weight"
              value={num(data.total_mass_kg / 1000, 1)}
              unit="t"
            />
            <Stat
              label="Brought in from outside"
              value={num(data.imported_mass_kg / 1000, 1)}
              unit="t"
              tone={data.imported_mass_kg > 0 ? "solar" : "good"}
              note="materials not available locally"
            />
            <Stat
              label="Delivered material cost"
              value={num(data.total_cost_inr / 100000, 2)}
              unit="lakh ₹"
            />
          </div>
          <table className="mt-4 w-full text-xs">
            <thead className="text-[var(--color-ink-faint)]">
              <tr>
                <th className="py-1 text-left font-normal">Item</th>
                <th className="py-1 text-left font-normal">Used in</th>
                <th className="py-1 text-right font-normal">m²</th>
                <th className="py-1 text-right font-normal">m³</th>
                <th className="py-1 text-right font-normal">kg</th>
                <th className="py-1 text-right font-normal">₹</th>
              </tr>
            </thead>
            <tbody className="tnum">
              {data.lines.map((line) => (
                <tr
                  key={`${line.kind}-${line.item_id}`}
                  className="border-t border-[var(--color-line-soft)]"
                >
                  <td className="py-1">
                    {line.name}
                    <span className="ml-1 text-[var(--color-ink-faint)]">
                      {KIND_LABEL[line.kind] ?? line.kind}
                      {line.locally_available === false ? ", brought in" : ""}
                    </span>
                  </td>
                  <td className="py-1">{line.used_in.join(", ")}</td>
                  <td className="py-1 text-right">{num(line.area_m2, 1)}</td>
                  <td className="py-1 text-right">
                    {line.volume_m3 != null ? num(line.volume_m3, 2) : "—"}
                  </td>
                  <td className="py-1 text-right">
                    {line.mass_kg != null
                      ? num(line.mass_kg, 0)
                      : "not weighed"}
                  </td>
                  <td className="py-1 text-right">{num(line.cost_inr, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(data.unweighed ?? []).length > 0 && (
            <p className="mt-2 text-[11px] text-[var(--color-ink-faint)]">
              Not in the weight: {data.unweighed!.join(", ")}. Their records
              carry no thickness or density to weigh them by.
            </p>
          )}
          {(data.notes ?? []).map((note) => (
            <p
              key={note}
              className="mt-1 text-[11px] text-[var(--color-ink-faint)]"
            >
              {note}
            </p>
          ))}
        </>
      )}
    </Panel>
  );
}

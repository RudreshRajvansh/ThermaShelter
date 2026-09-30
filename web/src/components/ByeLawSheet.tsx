import { useQuery } from "@tanstack/react-query";
import { api, type DesignSpec } from "../lib/api";
import { Empty, Failure, Loading, Panel } from "./Kit";
import { num } from "../lib/format";

function verdict(passes: boolean | null | undefined, mandatory: boolean): string {
  if (passes === true) return "meets";
  if (passes === false) return mandatory ? "fails" : "below";
  return "not assessed";
}

export function ByeLawSheet({ design, siteId }: { design: DesignSpec; siteId: string | null }) {
  const sheet = useQuery({
    queryKey: ["bye-law-sheet", design, siteId],
    queryFn: () => api.byeLawSheet(design, siteId),
    staleTime: Infinity,
    retry: false,
  });
  const data = sheet.data;
  return (
    <Panel
      title="Bye-law sheet"
      subtitle={data ? `${data.code}${data.climate_zone ? `, ${data.climate_zone} zone` : ""}` : "Eco-Niwas Samhita envelope checks"}
    >
      {sheet.error && <Failure error={sheet.error} />}
      {!data && !sheet.error && <Loading label="checking the envelope against the code…" />}
      {data && !data.assessed && <Empty title="Not assessed for this site" detail={data.zone_basis} />}
      {data && data.assessed && (
        <>
          <p
            className={`mb-3 rounded-md border px-3 py-2 text-sm ${
              data.mandatory_pass
                ? "border-[var(--color-good)]/40 bg-[var(--color-good)]/10 text-[var(--color-good)]"
                : "border-[var(--color-diesel)]/50 bg-[var(--color-diesel)]/10 text-[var(--color-danger-ink)]"
            }`}
          >
            {data.mandatory_pass
              ? "Meets every mandatory cold zone provision."
              : data.mandatory_pass === false
                ? "Fails at least one mandatory cold zone provision."
                : "A mandatory provision could not be assessed."}
          </p>
          <table className="w-full text-xs">
            <thead className="text-[var(--color-ink-faint)]">
              <tr>
                <th className="py-1 text-left font-normal">Clause</th>
                <th className="py-1 text-left font-normal">Check</th>
                <th className="py-1 text-right font-normal">Design</th>
                <th className="py-1 text-right font-normal">Limit</th>
                <th className="py-1 text-right font-normal">Result</th>
              </tr>
            </thead>
            <tbody className="tnum">
              {data.items?.map((item) => (
                <tr key={item.clause + item.title} className="border-t border-[var(--color-line-soft)] align-top">
                  <td className="py-1 pr-2">{item.clause}</td>
                  <td className="py-1 pr-2">
                    {item.title}
                    {!item.mandatory && <span className="ml-1 text-[var(--color-ink-faint)]">(advisory)</span>}
                    {item.note && <div className="text-[11px] text-[var(--color-ink-faint)]">{item.note}</div>}
                  </td>
                  <td className="py-1 text-right">
                    {item.value != null ? `${num(item.value, 2)} ${item.unit}` : "—"}
                  </td>
                  <td className="py-1 text-right">
                    {item.limit != null
                      ? `${item.comparison === "max" ? "≤" : "≥"} ${num(item.limit, 2)} ${item.unit}`
                      : "—"}
                  </td>
                  <td
                    className={`py-1 text-right ${
                      item.passes === false ? "text-[var(--color-danger-ink)]" : item.passes ? "text-[var(--color-good)]" : ""
                    }`}
                  >
                    {verdict(item.passes, item.mandatory)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-[var(--color-ink-faint)]">{data.zone_basis}</p>
          {(data.notes ?? []).map((note) => (
            <p key={note} className="mt-1 text-[11px] text-[var(--color-ink-faint)]">
              {note}
            </p>
          ))}
          <p className="mt-1 text-[11px] text-[var(--color-ink-faint)]">Source: {data.source.citation}</p>
        </>
      )}
    </Panel>
  );
}

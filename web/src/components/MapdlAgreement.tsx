import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type MapdlAbsent, type MapdlReport, type MapdlSet } from "../lib/api";
import { Empty, Failure, Loading, Stat } from "./Kit";
import { num } from "../lib/format";

const FACE_NAME: Record<string, string> = {
  south: "South wall",
  north: "North wall",
  east: "East wall",
  west: "West wall",
  roof: "Roof",
  floor: "Floor",
};
const axis = { fontSize: 11, fill: "var(--color-ink-faint)" };
const tooltip = {
  background: "var(--color-day)",
  color: "var(--color-ink)",
  border: "1px solid var(--color-line)",
  borderRadius: 8,
  fontSize: 12,
};

function isAbsent(value: MapdlSet | MapdlAbsent): value is MapdlAbsent {
  return value.available === false;
}

function Report({ report }: { report: MapdlReport }) {
  const fit = report.agreement;
  const hours = report.timestamps.map((stamp, index) => ({
    name: stamp.slice(5, 16).replace("T", " "),
    mapdl: report.mapdl_air_c[index],
    energyplus: report.energyplus_air_c[index],
    outdoor: report.outdoor_c[index],
  }));
  const convergence = report.convergence;
  const failedChecks = (report.checks ?? []).filter((check) => !check.passed);
  return (
    <div className="space-y-3 rounded-md border border-[var(--color-line)] bg-[var(--color-night)] p-3">
      <div className="text-xs font-medium">
        {report.solver} {report.version} · {report.label} · {report.level} mesh of{" "}
        {num(report.node_count, 0)} nodes · {report.cycles} passes over{" "}
        {num(fit.hours, 0)} hours, {report.substeps} steps an hour
      </div>
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat
          label="Room air, RMSE against EnergyPlus"
          value={num(fit.rmse_k, 2)}
          unit="K"
          tone={fit.meets_target ? "good" : "bad"}
          note={`target ${num(fit.target_rmse_k, 1)} K, ${fit.meets_target ? "met" : "not met"}`}
        />
        <Stat label="Mean bias" value={num(fit.bias_k, 2)} unit="K" note="MAPDL minus EnergyPlus" />
        <Stat label="Largest hourly gap" value={num(fit.max_abs_k, 2)} unit="K" />
        <Stat
          label="Mesh uncertainty"
          value={convergence?.gci_fine_k != null ? num(convergence.gci_fine_k, 2) : "not run"}
          unit={convergence?.gci_fine_k != null ? "K" : undefined}
          note={
            convergence
              ? convergence.order != null
                ? `GCI on three meshes, observed order ${num(convergence.order, 1)}${
                    convergence.time_step_difference_k != null
                      ? `; halving the step moves it ${num(convergence.time_step_difference_k, 2)} K`
                      : ""
                  }`
                : "the three meshes do not converge monotonically, so no GCI is given"
              : "run scripts/run_mapdl.py with --study for the three mesh check"
          }
        />
      </div>
      <div className="h-56">
        <ResponsiveContainer>
          <ComposedChart data={hours} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="var(--color-line-soft)" />
            <XAxis dataKey="name" tick={axis} stroke="var(--color-line)" minTickGap={40} />
            <YAxis tick={axis} stroke="var(--color-line)" />
            <Tooltip contentStyle={tooltip} formatter={(value: number) => `${num(value, 1)} °C`} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Line dataKey="energyplus" name="EnergyPlus room air" stroke="var(--color-cold)" strokeWidth={2} dot={false} />
            <Line dataKey="mapdl" name="MAPDL room air" stroke="var(--color-solar)" strokeWidth={2} dot={false} />
            <Line dataKey="outdoor" name="outdoor" stroke="var(--color-ink-faint)" strokeWidth={1} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <table className="w-full text-xs">
        <thead className="text-[var(--color-ink-faint)]">
          <tr>
            <th className="py-1 text-left font-normal">Heat through the inside face</th>
            <th className="py-1 text-right font-normal">MAPDL, kWh</th>
            <th className="py-1 text-right font-normal">EnergyPlus, kWh</th>
            <th className="py-1 text-right font-normal">Difference</th>
          </tr>
        </thead>
        <tbody className="tnum">
          {fit.faces?.map((face) => (
            <tr key={face.group} className="border-t border-[var(--color-line-soft)]">
              <td className="py-1">{FACE_NAME[face.group] ?? face.group}</td>
              <td className="py-1 text-right">{num(face.mapdl_kwh, 1)}</td>
              <td className="py-1 text-right">
                {face.energyplus_kwh != null ? num(face.energyplus_kwh, 1) : "not reported"}
              </td>
              <td className="py-1 text-right">
                {face.difference_percent != null ? `${num(face.difference_percent, 0)} %` : "n/a"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {(report.checks ?? []).length > 0 && (
        <div className="text-xs">
          <p className="mb-1 font-medium">
            Analytic checks of the element types{" "}
            {failedChecks.length === 0 ? "(all passed)" : `(${failedChecks.length} failed)`}
          </p>
          <ul className="space-y-1">
            {report.checks!.map((check) => (
              <li key={check.name} className={check.passed ? "" : "text-[var(--color-danger-ink)]"}>
                {check.description}: largest error {num(check.max_abs_k, 3)} K against{" "}
                {num(check.tolerance_k, 2)} K
              </li>
            ))}
          </ul>
        </div>
      )}
      {(report.levels ?? []).length > 1 && (
        <table className="w-full text-xs">
          <thead className="text-[var(--color-ink-faint)]">
            <tr>
              <th className="py-1 text-left font-normal">Mesh</th>
              <th className="py-1 text-right font-normal">Nodes</th>
              <th className="py-1 text-right font-normal">Steps an hour</th>
              <th className="py-1 text-right font-normal">Mean air, °C</th>
              <th className="py-1 text-right font-normal">Coldest, °C</th>
              <th className="py-1 text-right font-normal">RMSE, K</th>
            </tr>
          </thead>
          <tbody className="tnum">
            {report.levels!.map((level) => (
              <tr key={`${level.level}-${level.substeps}`} className="border-t border-[var(--color-line-soft)]">
                <td className="py-1">{level.level}</td>
                <td className="py-1 text-right">{num(level.node_count, 0)}</td>
                <td className="py-1 text-right">{level.substeps}</td>
                <td className="py-1 text-right">{num(level.mean_air_c, 2)}</td>
                <td className="py-1 text-right">{num(level.minimum_air_c, 2)}</td>
                <td className="py-1 text-right">{num(level.rmse_k, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(report.notes ?? []).map((note) => (
        <p key={note} className="text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
          {note}
        </p>
      ))}
    </div>
  );
}

export function MapdlAgreement({ designHash, siteId }: { designHash: string; siteId: string }) {
  const mapdl = useQuery({
    queryKey: ["mapdl", designHash, siteId],
    queryFn: () => api.mapdl(designHash, siteId),
    staleTime: Infinity,
    retry: false,
  });
  if (mapdl.isError) return <Failure error={mapdl.error} />;
  if (!mapdl.data) return <Loading label="checking for an ANSYS Mechanical APDL run…" />;
  const data = mapdl.data;
  const rejected = data.rejected ?? [];
  return (
    <div className="space-y-2">
      {isAbsent(data) ? (
        <Empty title="No ANSYS Mechanical APDL check for this design" detail={data.reason} />
      ) : (
        data.reports.map((report) => <Report key={report.label} report={report} />)
      )}
      {rejected.map((item) => (
        <p key={item} className="text-[11px] text-[var(--color-danger-ink)]">
          {item}
        </p>
      ))}
    </div>
  );
}

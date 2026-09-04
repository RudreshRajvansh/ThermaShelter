from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from .errors import ResultsUnavailable
from .schema import EngineWarning, Severity

_ERROR_LINE = re.compile(r"^\s*\*\*\s*(Severe|Fatal|Warning)\s*\*\*\s*(.*)$",
                         re.IGNORECASE)
_CONTINUATION = re.compile(r"^\s*\*\*\s*~~~\s*\*\*\s*(.*)$")
_SEVERITY = {"warning": Severity.WARNING, "severe": Severity.SEVERE,
             "fatal": Severity.FATAL}

_HOURLY = ("Hourly", "HVAC System Timestep", "Zone Timestep", "Detailed")

_KNOWN_BENIGN = (
    "GetSurfaceData: InterZone Surface Areas do not match as expected",
    "Output:Variable",
    "requested in this file but not used",
    "DisplayUnusedSchedules",
)

_INFORMATIONAL = (
    "Unused Schedules",
    "nominally unused constructions",
    "Each Unused construction is shown",
    "Report Variables were requested but not generated",
    "passed integrity testing",
    "Testing Individual",
    "No node connection errors were found",
    "Beginning Simulation",
    "Simulation Error Summary",
    "Warming up",
    "Initializing",
    "GetSurfaceData",
    "Key=*, VarName=",
    "This will show more details",
)

_EXPECTED = {
    "Site:GroundTemperature:BuildingSurface: Some values fall outside the range":
        ("Ladakh ground freezes seasonally, so monthly ground temperatures below the "
         "15 to 25 C range EnergyPlus expects are correct here rather than a mistake. "
         "The values came from the Layer 0 site record."),
}


def _classify(message: str, severity: Severity) -> tuple[Severity, str]:
    if severity in (Severity.SEVERE, Severity.FATAL):
        return severity, message
    for marker, explanation in _EXPECTED.items():
        if marker in message:
            return Severity.INFO, f"{message} {explanation}"
    if any(marker in message for marker in _INFORMATIONAL):
        return Severity.INFO, message
    return severity, message


@dataclass
class SimulationOutput:
    sql_path: Path
    frame: pd.DataFrame
    units: dict[tuple[str, str], str] = field(default_factory=dict)
    warnings: list[EngineWarning] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.frame.empty

    def variables(self) -> tuple[str, ...]:
        return tuple(sorted(self.frame["name"].unique()))

    def keys_for(self, name: str) -> tuple[str, ...]:
        subset = self.frame[self.frame["name"] == name]
        return tuple(sorted(subset["key"].unique()))

    def series(self, name: str, key: str | None = None,
               required: bool = True) -> pd.Series | None:
        subset = self.frame[self.frame["name"] == name]
        if key is not None:
            subset = subset[subset["key"] == key.upper()]
        if subset.empty:
            if required:
                raise ResultsUnavailable(
                    f"{self.sql_path.name} holds no series named {name!r}"
                    + (f" for key {key!r}" if key else "")
                    + f"; available variables are {self.variables()}")
            return None
        pivot = subset.pivot_table(index="timestamp", values="value", aggfunc="mean")
        result = pivot["value"].sort_index()
        result.name = name if key is None else f"{name}::{key}"
        return result

    def first_available(self, names: Sequence[str], key: str | None = None
                        ) -> tuple[str, pd.Series] | None:
        for name in names:
            series = self.series(name, key, required=False)
            if series is not None and not series.dropna().empty:
                return name, series
        return None

    def wide(self, name: str) -> pd.DataFrame:
        subset = self.frame[self.frame["name"] == name]
        if subset.empty:
            return pd.DataFrame()
        return subset.pivot_table(index="timestamp", columns="key", values="value",
                                  aggfunc="mean").sort_index()

    def unit(self, name: str, key: str | None = None) -> str:
        if key is not None and (name, key.upper()) in self.units:
            return self.units[(name, key.upper())]
        for (candidate, _), unit in self.units.items():
            if candidate == name:
                return unit
        return ""


def _build_timestamps(frame: pd.DataFrame, nominal_year: int) -> pd.Series:
    years = pd.Series(np.full(len(frame), nominal_year), index=frame.index)
    months = frame["Month"].astype(int)
    days = frame["Day"].astype(int)
    hours = frame["Hour"].astype(int)
    minutes = frame["Minute"].fillna(0).astype(int)
    base = pd.to_datetime({"year": years, "month": months, "day": days,
                           "hour": np.zeros(len(frame), dtype=int)},
                          errors="coerce")
    if base.isna().any():
        fallback = frame["Year"].fillna(nominal_year).astype(int)
        fallback = fallback.where(fallback > 1900, nominal_year)
        base = pd.to_datetime({"year": fallback, "month": months, "day": days,
                               "hour": np.zeros(len(frame), dtype=int)},
                              errors="coerce")
    offset = pd.to_timedelta(np.clip(hours.to_numpy() - 1, 0, 23), unit="h")
    sub_hour = pd.to_timedelta(np.where(minutes.to_numpy() >= 60, 0,
                                        minutes.to_numpy()), unit="m")
    return base + offset + sub_hour


def read_sql(sql_path: str | Path, nominal_year: int = 2019,
             frequencies: Sequence[str] = _HOURLY) -> SimulationOutput:
    path = Path(sql_path)
    if not path.is_file():
        raise ResultsUnavailable(f"EnergyPlus produced no SQL output at {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for required in ("ReportData", "ReportDataDictionary", "Time"):
            if required not in tables:
                raise ResultsUnavailable(
                    f"{path.name} has no {required} table; the run did not get as far "
                    "as reporting results")
        placeholders = ",".join("?" for _ in frequencies)
        query = (
            "SELECT t.Year, t.Month, t.Day, t.Hour, t.Minute, "
            "       d.KeyValue, d.Name, d.Units, rd.Value "
            "FROM ReportData rd "
            "JOIN Time t ON t.TimeIndex = rd.TimeIndex "
            "JOIN ReportDataDictionary d "
            "  ON d.ReportDataDictionaryIndex = rd.ReportDataDictionaryIndex "
            f"WHERE d.ReportingFrequency IN ({placeholders}) "
            "  AND (t.WarmupFlag IS NULL OR t.WarmupFlag = 0) "
            "  AND t.Month IS NOT NULL AND t.Day IS NOT NULL AND t.Hour IS NOT NULL")
        frame = pd.read_sql_query(query, connection, params=list(frequencies))
    finally:
        connection.close()

    if frame.empty:
        return SimulationOutput(sql_path=path, frame=pd.DataFrame(
            columns=["timestamp", "key", "name", "value"]))

    frame["timestamp"] = _build_timestamps(frame, nominal_year)
    units = {(str(row.Name), str(row.KeyValue).upper()): str(row.Units)
             for row in frame[["Name", "KeyValue", "Units"]]
             .drop_duplicates().itertuples()}
    tidy = pd.DataFrame({
        "timestamp": frame["timestamp"],
        "key": frame["KeyValue"].astype(str).str.upper(),
        "name": frame["Name"].astype(str),
        "value": pd.to_numeric(frame["Value"], errors="coerce"),
    })
    tidy = tidy.dropna(subset=["timestamp"])
    return SimulationOutput(sql_path=path, frame=tidy, units=units)


def _read_error_table(connection: sqlite3.Connection) -> list[EngineWarning]:
    collected: list[EngineWarning] = []
    try:
        rows = connection.execute(
            "SELECT ErrorType, ErrorMessage, Count FROM Errors").fetchall()
    except sqlite3.DatabaseError:
        return collected
    for error_type, message, count in rows:
        severity = {0: Severity.WARNING, 1: Severity.SEVERE,
                    2: Severity.FATAL}.get(int(error_type or 0), Severity.WARNING)
        collected.append(EngineWarning(
            code="energyplus_error_table", severity=severity, origin="energyplus",
            message=str(message).strip(), count=max(1, int(count or 1))))
    return collected


def parse_err_file(err_path: str | Path) -> list[EngineWarning]:
    path = Path(err_path)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="latin-1").splitlines()
    except OSError:
        return []
    collected: list[EngineWarning] = []
    current: list[str] | None = None
    current_severity = Severity.WARNING
    for line in lines:
        match = _ERROR_LINE.match(line)
        if match:
            if current:
                collected.append(_finish(current, current_severity))
            current_severity = _SEVERITY[match.group(1).lower()]
            current = [match.group(2).strip()]
            continue
        continuation = _CONTINUATION.match(line)
        if continuation and current is not None:
            current.append(continuation.group(1).strip())
            continue
        if current:
            collected.append(_finish(current, current_severity))
            current = None
    if current:
        collected.append(_finish(current, current_severity))
    return _deduplicate(collected)


def _finish(parts: list[str], severity: Severity) -> EngineWarning:
    message = " ".join(part for part in parts if part).strip()
    message = message or "unspecified EnergyPlus message"
    classified, message = _classify(message, severity)
    return EngineWarning(code="energyplus", severity=classified, origin="energyplus",
                         message=message[:2000])


def _deduplicate(items: Iterable[EngineWarning]) -> list[EngineWarning]:
    seen: dict[tuple[str, str], EngineWarning] = {}
    order: list[tuple[str, str]] = []
    for item in items:
        key = (item.severity.value, item.message)
        if key in seen:
            existing = seen[key]
            seen[key] = existing.model_copy(update={"count": existing.count + 1})
        else:
            seen[key] = item
            order.append(key)
    return [seen[key] for key in order]


def blocking(warnings: Sequence[EngineWarning]) -> list[EngineWarning]:
    return [item for item in warnings
            if item.severity in (Severity.SEVERE, Severity.FATAL)
            and not any(benign in item.message for benign in _KNOWN_BENIGN)]


def hourly_alignment(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    resampled = series.resample("1h").mean()
    return resampled.dropna()

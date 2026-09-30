from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from shelter_engine.schema import SimulationResult, SurfaceResults

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    design_hash TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    design_name TEXT NOT NULL,
    arrangement TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    runtime_s REAL NOT NULL,
    engine_version TEXT NOT NULL,
    energyplus_version TEXT NOT NULL,
    minimum_overnight_c REAL,
    degree_hours_total_kh REAL,
    heating_demand_kwh REAL,
    total_cost_inr REAL,
    has_blocking_warnings INTEGER NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_site_created
    ON runs (site_id, created_at DESC);

CREATE TABLE IF NOT EXISTS surface_results (
    design_hash TEXT PRIMARY KEY,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    surfaces INTEGER NOT NULL,
    hours INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (design_hash) REFERENCES runs (design_hash) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    site_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS optimisations (
    job_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    evaluated INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    spec_json TEXT NOT NULL,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
    post_id TEXT NOT NULL,
    lang TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    card_json TEXT NOT NULL,
    PRIMARY KEY (post_id, lang)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class RunSummary:
    design_hash: str
    site_id: str
    design_name: str
    arrangement: str
    period_start: str
    period_end: str
    created_at: str
    runtime_s: float
    minimum_overnight_c: float | None
    degree_hours_total_kh: float | None
    heating_demand_kwh: float | None
    total_cost_inr: float | None
    has_blocking_warnings: bool


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=30.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()

    def save_run(self, result: SimulationResult) -> None:
        payload = result.model_dump(mode="json")
        payload.pop("surfaces", None)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO runs (design_hash, site_id, design_name, "
                "arrangement, period_start, period_end, created_at, runtime_s, "
                "engine_version, energyplus_version, minimum_overnight_c, "
                "degree_hours_total_kh, heating_demand_kwh, total_cost_inr, "
                "has_blocking_warnings, result_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (result.design_hash, result.site_id, result.design.name,
                 result.design.arrangement.value, result.period.start.isoformat(),
                 result.period.end.isoformat(), _now(), result.runtime_s,
                 result.engine_version, result.energyplus_version,
                 result.comfort.minimum_overnight_operative_c,
                 result.comfort.degree_hours_total_kh,
                 result.energy.heating_demand_kwh, result.cost.total_inr,
                 int(result.has_blocking_warnings),
                 json.dumps(payload, separators=(",", ":"))))
            if result.surfaces is not None:
                connection.execute(
                    "INSERT OR REPLACE INTO surface_results (design_hash, "
                    "period_start, period_end, surfaces, hours, payload_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (result.design_hash,
                     result.surfaces.period.start.isoformat(),
                     result.surfaces.period.end.isoformat(),
                     len(result.surfaces.surfaces),
                     len(result.surfaces.timestamps),
                     json.dumps(result.surfaces.model_dump(mode="json"),
                                separators=(",", ":"))))

    def load_run(self, design_hash: str,
                 with_surfaces: bool = True) -> SimulationResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM runs WHERE design_hash = ?",
                (design_hash,)).fetchone()
            if row is None:
                return None
            payload = json.loads(row["result_json"])
            if with_surfaces:
                surfaces = connection.execute(
                    "SELECT payload_json FROM surface_results WHERE design_hash = ?",
                    (design_hash,)).fetchone()
                if surfaces is not None:
                    payload["surfaces"] = json.loads(surfaces["payload_json"])
        return SimulationResult.model_validate(payload)

    def load_surfaces(self, design_hash: str) -> SurfaceResults | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM surface_results WHERE design_hash = ?",
                (design_hash,)).fetchone()
        if row is None:
            return None
        return SurfaceResults.model_validate(json.loads(row["payload_json"]))

    def list_runs(self, site_id: str | None = None,
                  limit: int = 100) -> list[RunSummary]:
        query = ("SELECT design_hash, site_id, design_name, arrangement, "
                 "period_start, period_end, created_at, runtime_s, "
                 "minimum_overnight_c, degree_hours_total_kh, heating_demand_kwh, "
                 "total_cost_inr, has_blocking_warnings FROM runs")
        params: list[Any] = []
        if site_id:
            query += " WHERE site_id = ?"
            params.append(site_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [RunSummary(
            design_hash=row["design_hash"], site_id=row["site_id"],
            design_name=row["design_name"], arrangement=row["arrangement"],
            period_start=row["period_start"], period_end=row["period_end"],
            created_at=row["created_at"], runtime_s=row["runtime_s"],
            minimum_overnight_c=row["minimum_overnight_c"],
            degree_hours_total_kh=row["degree_hours_total_kh"],
            heating_demand_kwh=row["heating_demand_kwh"],
            total_cost_inr=row["total_cost_inr"],
            has_blocking_warnings=bool(row["has_blocking_warnings"]))
            for row in rows]

    def count_runs(self) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) AS total FROM runs").fetchone()["total"])

    def save_project(self, identifier: str, name: str, site_id: str,
                     payload: dict[str, Any]) -> None:
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT created_at FROM projects WHERE id = ?",
                (identifier,)).fetchone()
            created = existing["created_at"] if existing else now
            connection.execute(
                "INSERT OR REPLACE INTO projects (id, name, site_id, created_at, "
                "updated_at, payload_json) VALUES (?,?,?,?,?,?)",
                (identifier, name, site_id, created, now,
                 json.dumps(payload, separators=(",", ":"))))

    def load_project(self, identifier: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, name, site_id, created_at, updated_at, payload_json "
                "FROM projects WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"], "site_id": row["site_id"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "payload": json.loads(row["payload_json"])}

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, site_id, created_at, updated_at FROM projects "
                "ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def delete_project(self, identifier: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM projects WHERE id = ?",
                                        (identifier,))
            return cursor.rowcount > 0

    def save_post(self, identifier: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO posts (id, updated_at, payload_json) VALUES (?,?,?)",
                (identifier, _now(), json.dumps(payload, separators=(",", ":"))))

    def load_post(self, identifier: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM posts WHERE id = ?",
                                     (identifier,)).fetchone()
        return None if row is None else json.loads(row["payload_json"])

    def list_posts(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM posts ORDER BY id").fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def delete_post(self, identifier: str) -> bool:
        with self._connect() as connection:
            connection.execute("DELETE FROM cards WHERE post_id = ?", (identifier,))
            cursor = connection.execute("DELETE FROM posts WHERE id = ?", (identifier,))
            return cursor.rowcount > 0

    def save_card(self, post_id: str, lang: str, issued_at: str, card: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO cards (post_id, lang, issued_at, card_json) "
                "VALUES (?,?,?,?)",
                (post_id, lang, issued_at, json.dumps(card, separators=(",", ":"))))

    def load_card(self, post_id: str, lang: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT card_json FROM cards WHERE post_id = ? AND lang = ?",
                (post_id, lang)).fetchone()
        return None if row is None else json.loads(row["card_json"])

    def create_optimisation(self, job_id: str, site_id: str,
                            spec: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO optimisations (job_id, site_id, status, "
                "created_at, spec_json) VALUES (?,?,?,?,?)",
                (job_id, site_id, "running", _now(),
                 json.dumps(spec, separators=(",", ":"))))

    def publish_optimisation_stage(self, job_id: str, result: dict[str, Any],
                                   evaluated: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE optimisations SET evaluated = ?, result_json = ? "
                "WHERE job_id = ? AND status = 'running'",
                (evaluated, json.dumps(result, separators=(",", ":")), job_id))

    def finish_optimisation(self, job_id: str, result: dict[str, Any] | None,
                            message: str | None = None,
                            evaluated: int = 0) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE optimisations SET status = ?, finished_at = ?, "
                "evaluated = ?, message = ?, result_json = ? WHERE job_id = ?",
                ("done" if result is not None else "failed", _now(), evaluated,
                 message,
                 json.dumps(result, separators=(",", ":")) if result else None,
                 job_id))

    def load_optimisation(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT job_id, site_id, status, created_at, finished_at, "
                "evaluated, message, result_json FROM optimisations "
                "WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        raw = payload.pop("result_json", None)
        payload["result"] = json.loads(raw) if raw else None
        return payload

    def list_optimisations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id, site_id, status, created_at, finished_at, "
                "evaluated, message FROM optimisations "
                "ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),)).fetchall()
        return [dict(row) for row in rows]

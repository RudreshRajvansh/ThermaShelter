from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence, TypeVar

from .config import SETTINGS, EnergyPlusInstall, EngineSettings, find_energyplus
from .errors import SimulationFailed, SimulationTimeout, WeatherError
from .idf import IdfBundle
from .results import SimulationOutput, blocking, parse_err_file, read_sql
from .schema import EngineWarning, Severity

T = TypeVar("T")

OUTPUT_PREFIX = "eplus"
SQL_NAME = f"{OUTPUT_PREFIX}out.sql"
ERR_NAME = f"{OUTPUT_PREFIX}out.err"
INPUT_NAME = "in.idf"


@dataclass
class RunArtifacts:
    run_dir: Path
    idf_path: Path
    sql_path: Path
    err_path: Path
    returncode: int
    runtime_s: float
    stdout: str = ""
    stderr: str = ""
    warnings: list[EngineWarning] = field(default_factory=list)

    def cleanup(self) -> None:
        shutil.rmtree(self.run_dir, ignore_errors=True)


def _safe_name(label: str) -> str:
    keep = [character if character.isalnum() or character in "-_." else "_"
            for character in label]
    return "".join(keep)[:60] or "run"


def run_energyplus(bundle: IdfBundle, epw_path: str | Path, label: str = "run",
                   settings: EngineSettings | None = None,
                   install: EnergyPlusInstall | None = None,
                   keep_files: bool | None = None) -> RunArtifacts:
    settings = settings or SETTINGS
    install = install or find_energyplus()
    weather = Path(epw_path)
    if not weather.is_file():
        raise WeatherError(f"weather file not found at {weather}")

    settings.ensure_work_dir()
    run_dir = Path(tempfile.mkdtemp(prefix=f"{_safe_name(label)}_",
                                    dir=str(settings.work_dir)))
    idf_path = run_dir / INPUT_NAME
    idf_path.write_text(bundle.text, encoding="utf-8")

    command = [
        str(install.executable),
        "--idd", str(install.idd),
        "--weather", str(weather.resolve()),
        "--output-directory", str(run_dir),
        "--output-prefix", OUTPUT_PREFIX,
        str(idf_path),
    ]
    environment = dict(os.environ)
    environment.setdefault("ENERGYPLUS_WEATHER", str(weather.resolve()))

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command, cwd=str(run_dir), capture_output=True, text=True,
            timeout=settings.run_timeout_s, env=environment, check=False)
    except subprocess.TimeoutExpired as error:
        runtime = time.perf_counter() - started
        raise SimulationTimeout(
            f"EnergyPlus exceeded the {settings.run_timeout_s:.0f} s timeout after "
            f"{runtime:.1f} s for {label}. Shorten the run period, raise "
            "SHELTER_ENGINE_TIMEOUT, or check for a solver that is failing to converge.",
            run_dir=str(run_dir)) from error

    runtime = time.perf_counter() - started
    err_path = run_dir / ERR_NAME
    sql_path = run_dir / SQL_NAME
    warnings = parse_err_file(err_path)

    artifacts = RunArtifacts(
        run_dir=run_dir, idf_path=idf_path, sql_path=sql_path, err_path=err_path,
        returncode=completed.returncode, runtime_s=runtime,
        stdout=completed.stdout or "", stderr=completed.stderr or "",
        warnings=warnings)

    if completed.returncode != 0 or not sql_path.is_file():
        fatal = [item.message for item in warnings if item.severity is Severity.FATAL]
        severe = [item.message for item in warnings if item.severity is Severity.SEVERE]
        detail = fatal[0] if fatal else (severe[0] if severe else
                                         (completed.stderr or completed.stdout or
                                          "no diagnostic message was produced"))
        raise SimulationFailed(
            f"EnergyPlus failed for {label} with exit code {completed.returncode}: "
            f"{detail.strip()[:600]} (run directory kept at {run_dir})",
            returncode=completed.returncode, severe=severe, fatal=fatal,
            run_dir=str(run_dir))

    keep = settings.keep_run_files if keep_files is None else keep_files
    if not keep:
        artifacts.warnings = warnings
    return artifacts


def simulate(bundle: IdfBundle, epw_path: str | Path, label: str = "run",
             nominal_year: int = 2019, settings: EngineSettings | None = None,
             install: EnergyPlusInstall | None = None
             ) -> tuple[SimulationOutput, RunArtifacts]:
    settings = settings or SETTINGS
    artifacts = run_energyplus(bundle, epw_path, label, settings, install)
    try:
        output = read_sql(artifacts.sql_path, nominal_year=nominal_year)
    except Exception:
        raise
    combined = list(artifacts.warnings)
    seen = {(item.severity.value, item.message) for item in combined}
    for item in output.warnings:
        if (item.severity.value, item.message) not in seen:
            combined.append(item)
    output.warnings = combined
    if output.is_empty:
        raise SimulationFailed(
            f"EnergyPlus completed for {label} but reported no hourly results. "
            "The run period may fall outside the weather file, or every requested "
            f"output variable may be unavailable (run directory {artifacts.run_dir}).",
            returncode=artifacts.returncode, run_dir=str(artifacts.run_dir))
    if not settings.keep_run_files:
        artifacts.cleanup()
    return output, artifacts


def run_parallel(tasks: Sequence[Callable[[], T]],
                 settings: EngineSettings | None = None,
                 on_error: Callable[[int, Exception], T | None] | None = None
                 ) -> list[T | None]:
    settings = settings or SETTINGS
    if not tasks:
        return []
    workers = min(settings.max_parallel_runs, len(tasks))
    results: list[T | None] = [None] * len(tasks)
    if workers == 1:
        for index, task in enumerate(tasks):
            try:
                results[index] = task()
            except Exception as error:
                if on_error is None:
                    raise
                results[index] = on_error(index, error)
        return results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(task): index for index, task in enumerate(tasks)}
        for future, index in futures.items():
            try:
                results[index] = future.result()
            except Exception as error:
                if on_error is None:
                    raise
                results[index] = on_error(index, error)
    return results


def report_blocking(warnings: Iterable[EngineWarning]) -> list[EngineWarning]:
    return blocking(list(warnings))

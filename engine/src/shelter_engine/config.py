from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .errors import ConfigurationError, EnergyPlusNotFound

_VERSION_RE = re.compile(r"(\d+)[.\-](\d+)(?:[.\-](\d+))?")
_EXE_NAME = "energyplus.exe" if sys.platform == "win32" else "energyplus"

_SEARCH_GLOBS = (
    "C:/EnergyPlusV*",
    "C:/Program Files/EnergyPlusV*",
    "C:/Program Files (x86)/EnergyPlusV*",
    "/usr/local/EnergyPlus-*",
    "/usr/local/bin/EnergyPlus-*",
    "/opt/EnergyPlus-*",
    "/Applications/EnergyPlus-*",
)

MIN_SUPPORTED = (9, 4)


def _version_of(name: str) -> tuple[int, int, int]:
    match = _VERSION_RE.search(name)
    if not match:
        return (0, 0, 0)
    major, minor, patch = match.group(1), match.group(2), match.group(3)
    return (int(major), int(minor), int(patch or 0))


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("ENERGYPLUS_DIR") or os.environ.get("ENERGYPLUS_ROOT")
    if env_root:
        roots.append(Path(env_root))
    on_path = shutil.which("energyplus")
    if on_path:
        roots.append(Path(on_path).resolve().parent)
    for pattern in _SEARCH_GLOBS:
        base = Path(pattern).parent
        glob = Path(pattern).name
        try:
            if base.is_dir():
                roots.extend(sorted(base.glob(glob)))
        except OSError:
            continue
    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


@dataclass(frozen=True)
class EnergyPlusInstall:
    root: Path
    executable: Path
    idd: Path
    version: tuple[int, int, int]

    @property
    def version_string(self) -> str:
        return ".".join(str(part) for part in self.version)

    @property
    def idf_version(self) -> str:
        return f"{self.version[0]}.{self.version[1]}"


def _install_at(root: Path) -> EnergyPlusInstall | None:
    if not root.is_dir():
        return None
    executable = root / _EXE_NAME
    if not executable.is_file():
        return None
    idd = root / "Energy+.idd"
    if not idd.is_file():
        return None
    version = _version_of(root.name)
    if version == (0, 0, 0):
        version = _idd_version(idd)
    return EnergyPlusInstall(root=root, executable=executable, idd=idd, version=version)


def _idd_version(idd: Path) -> tuple[int, int, int]:
    try:
        with idd.open("r", encoding="latin-1") as handle:
            for _ in range(200):
                line = handle.readline()
                if not line:
                    break
                if "IDD_Version" in line:
                    return _version_of(line)
    except OSError:
        pass
    return (0, 0, 0)


@lru_cache(maxsize=1)
def find_energyplus() -> EnergyPlusInstall:
    found: list[EnergyPlusInstall] = []
    for root in _candidate_roots():
        install = _install_at(root)
        if install is not None:
            found.append(install)
    if not found:
        raise EnergyPlusNotFound(
            "EnergyPlus was not found. Install it from energyplus.net and either add it "
            "to PATH or set ENERGYPLUS_DIR to the installation directory "
            "(the directory containing energyplus"
            + (".exe" if sys.platform == "win32" else "")
            + " and Energy+.idd)."
        )
    found.sort(key=lambda item: item.version, reverse=True)
    best = found[0]
    if best.version < MIN_SUPPORTED:
        raise ConfigurationError(
            f"EnergyPlus {best.version_string} at {best.root} is older than the minimum "
            f"supported {MIN_SUPPORTED[0]}.{MIN_SUPPORTED[1]}."
        )
    return best


def energyplus_available() -> bool:
    try:
        find_energyplus()
    except ConfigurationError:
        return False
    return True


@dataclass
class EngineSettings:
    work_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("SHELTER_ENGINE_WORK_DIR", Path.home() / ".shelter_engine" / "runs")))
    keep_run_files: bool = field(
        default_factory=lambda: os.environ.get("SHELTER_ENGINE_KEEP_RUNS", "0") == "1")
    run_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("SHELTER_ENGINE_TIMEOUT", "600")))
    max_parallel_runs: int = field(
        default_factory=lambda: max(1, int(os.environ.get(
            "SHELTER_ENGINE_WORKERS", str(max(1, (os.cpu_count() or 2) - 1))))))
    data_dir: Path = field(default_factory=lambda: Path(__file__).parent / "data")

    def __post_init__(self) -> None:
        self.work_dir = Path(self.work_dir)
        self.data_dir = Path(self.data_dir)
        if self.run_timeout_s <= 0:
            raise ConfigurationError("run_timeout_s must be positive")
        if self.max_parallel_runs < 1:
            raise ConfigurationError("max_parallel_runs must be >= 1")

    def ensure_work_dir(self) -> Path:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        return self.work_dir


SETTINGS = EngineSettings()

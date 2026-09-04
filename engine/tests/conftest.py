from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import fixtures  # noqa: E402
from shelter_engine.config import energyplus_available  # noqa: E402
from synthetic_epw import write_synthetic_epw  # noqa: E402


@pytest.fixture(scope="session")
def library():
    return fixtures.material_library()


@pytest.fixture(scope="session")
def epw(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("weather") / "synthetic_leh.epw"
    return write_synthetic_epw(target)


@pytest.fixture(scope="session")
def site(epw):
    return fixtures.site(str(epw))


@pytest.fixture(scope="session")
def has_energyplus() -> bool:
    return energyplus_available()


requires_energyplus = pytest.mark.skipif(
    not energyplus_available(),
    reason="EnergyPlus is not installed; set ENERGYPLUS_DIR or add it to PATH")

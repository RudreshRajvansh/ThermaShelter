from __future__ import annotations

import os

GRID_DEG = 0.1
OFFLINE_MESSAGE = ("Outside calls are switched off on this machine (SHELTER_OFFLINE), so "
                   "only data already in the region pack is used.")


def offline() -> bool:
    return os.environ.get("SHELTER_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


def grid(value_deg: float) -> str:
    return f"{round(value_deg / GRID_DEG) * GRID_DEG:.1f}"


def endpoint(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default

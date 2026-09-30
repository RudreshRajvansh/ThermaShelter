from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shelter_engine.calibration import fit, fit_mask, read_logger_csv, rmse
from shelter_engine.errors import EngineError


def logger_text(hours: int = 72, heater: bool = False) -> str:
    start = pd.Timestamp("2026-12-01 00:00")
    lines = ["timestamp,temperature_c" + (",heater_on" if heater else "")]
    for index in range(hours * 2):
        moment = start + pd.to_timedelta(30 * index, unit="min")
        value = 10.0 + 3.0 * np.sin(2 * np.pi * index / 48.0)
        extra = f",{1 if heater and 18 <= moment.hour <= 20 else 0}" if heater else ""
        lines.append(f"{moment:%Y-%m-%d %H:%M},{value:.2f}{extra}")
    return "\n".join(lines) + "\n"


def test_logger_files_are_read_hourly_and_checked():
    record = read_logger_csv(logger_text())
    assert len(record.temperature_c) == 72 and record.heater_on is None
    with pytest.raises(EngineError, match="timestamp"):
        read_logger_csv("when,value\n2026-12-01,3\n")
    with pytest.raises(EngineError, match="unreadable"):
        read_logger_csv("timestamp,temperature_c\nnot a date,3\n")
    with pytest.raises(EngineError, match="at least 24"):
        read_logger_csv(logger_text(hours=10))
    with pytest.raises(EngineError, match="-60 to 60"):
        read_logger_csv(logger_text().replace("10.00", "99.00"))
    with pytest.raises(EngineError, match="local time"):
        read_logger_csv("timestamp,temperature_c\n2026-12-01T00:00+05:30,3\n")


def test_heater_hours_and_the_two_after_are_left_out():
    record = read_logger_csv(logger_text(heater=True))
    mask = fit_mask(record)
    assert not mask[pd.Timestamp("2026-12-01 19:00")]
    assert not mask[pd.Timestamp("2026-12-01 22:00")]
    assert mask[pd.Timestamp("2026-12-01 23:00")]
    assert mask[pd.Timestamp("2026-12-01 12:00")]


def test_the_fit_recovers_the_leakage_and_mass_that_made_the_data():
    record = read_logger_csv(logger_text(hours=96))
    truth = record.temperature_c

    base = truth.mean()

    def model(ach: float, mass: float) -> pd.Series:
        return base + 2.0 * (ach - 1.2) + (truth - base) * (1.3 / mass)

    result = fit(model, record, start_ach=0.5)
    assert result.rmse_before_k > 1.0
    assert result.rmse_after_k < 0.1
    assert result.rmse_after_k <= result.rmse_before_k
    assert result.evaluations <= 60
    assert rmse(model(result.infiltration_ach, result.mass_multiplier), truth,
                fit_mask(record)) == pytest.approx(result.rmse_after_k)


def test_a_fit_never_ends_worse_than_it_started():
    record = read_logger_csv(logger_text())
    result = fit(lambda ach, mass: record.temperature_c, record, start_ach=0.7)
    assert result.rmse_after_k == pytest.approx(0.0)
    assert result.infiltration_ach == pytest.approx(0.7)

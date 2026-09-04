from __future__ import annotations


class EngineError(Exception):
    pass


class ConfigurationError(EngineError):
    pass


class EnergyPlusNotFound(ConfigurationError):
    pass


class WeatherError(EngineError):
    pass


class MaterialError(EngineError):
    pass


class GeometryError(EngineError):
    pass


class SimulationFailed(EngineError):
    def __init__(self, message: str, *, returncode: int | None = None,
                 severe: list[str] | None = None, fatal: list[str] | None = None,
                 run_dir: str | None = None):
        super().__init__(message)
        self.returncode = returncode
        self.severe = severe or []
        self.fatal = fatal or []
        self.run_dir = run_dir


class SimulationTimeout(SimulationFailed):
    pass


class ResultsUnavailable(EngineError):
    pass


class OptimisationError(EngineError):
    pass

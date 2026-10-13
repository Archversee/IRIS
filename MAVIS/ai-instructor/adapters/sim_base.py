"""Sim-agnostic telemetry model and adapter interface."""
from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict
from typing import Protocol, runtime_checkable


@dataclass
class Telemetry:
    t: float = field(default_factory=time.time)
    lat: float = 0.0
    lon: float = 0.0
    alt_msl: float = 0.0        # ft
    alt_agl: float = 0.0        # ft
    ias: float = 0.0            # kt
    vs: float = 0.0             # ft/min
    hdg: float = 0.0            # deg true
    bank: float = 0.0           # deg, + right
    pitch: float = 0.0          # deg, + up
    flaps_deg: float = 0.0
    on_ground: bool = True
    rpm: float = 0.0
    # control positions as the sim sees them (-1..1 / 0..1) - device-agnostic truth
    aileron: float = 0.0
    elevator: float = 0.0
    rudder: float = 0.0
    throttle: float = 0.0
    brake_left: float = 0.0     # 0..1 toe-brake position (0 = off)
    brake_right: float = 0.0
    local_time_h: float = 12.0  # sim local time, hours
    # cockpit lever/wheel settings, for IRIS's live telemetry view; None when
    # an adapter doesn't read them (IRIS then derives flaps from flaps_deg)
    flaps_handle_pct: float | None = None   # 0..100
    elevator_trim_pct: float | None = None  # -100..100, + nose up
    rudder_trim_pct: float | None = None    # -100..100, + nose right (aircraft with no rudder trim read 0)
    aileron_trim_pct: float | None = None   # -100..100, + right wing down (ditto)

    def as_dict(self) -> dict:
        return asdict(self)


@runtime_checkable
class SimAdapter(Protocol):
    """Implement per sim (MSFS, Prepar3D, ...). All units per Telemetry."""

    def connect(self) -> None: ...
    def get_telemetry(self) -> Telemetry: ...
    def set_local_time(self, hour: int, minute: int) -> bool: ...
    def close(self) -> None: ...

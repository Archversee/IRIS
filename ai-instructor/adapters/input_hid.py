"""Raw HID capture for stick/throttle/rudder via pygame.

Device profiles (YAML) map physical axes -> logical controls, so swapping
hardware is a config change. If pygame or the devices are missing, the app
degrades to sim-side control variables only (Telemetry.aileron etc.).
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field, asdict

log = logging.getLogger(__name__)


@dataclass
class ControlSample:
    t: float = field(default_factory=time.time)
    pitch: float = 0.0     # -1 push .. +1 pull
    roll: float = 0.0      # -1 left .. +1 right
    yaw: float = 0.0
    throttle: float = 0.0  # 0..1
    ptt: bool = False      # push-to-talk button held (joystick)
    available: bool = False

    def as_dict(self): return asdict(self)


class HIDInput:
    def __init__(self, profiles: list[dict]):
        self.profiles = profiles
        self.map = []      # (joystick, logical_name, axis_idx, sign)
        self.btn_map = []  # (joystick, logical_name, button_idx) e.g. ptt
        self.ok = False

    def connect(self):
        try:
            import pygame
            pygame.init()
            pygame.joystick.init()
        except Exception as e:
            log.warning("pygame unavailable, raw HID disabled: %s", e)
            return
        sticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
        for js in sticks:
            js.init()
            for prof in self.profiles:
                if prof["match"]["name_contains"].lower() in js.get_name().lower():
                    inv = prof.get("invert", {})
                    for logical, axis in prof["axes"].items():
                        sign = -1.0 if inv.get(logical) else 1.0
                        self.map.append((js, logical, axis, sign))
                    for logical, btn in (prof.get("buttons") or {}).items():
                        self.btn_map.append((js, logical, int(btn)))
                    log.info("mapped %s via profile %s", js.get_name(), prof["name"])
        self.ok = bool(self.map or self.btn_map)
        self._pygame = pygame

    def poll(self) -> ControlSample:
        if not self.ok:
            return ControlSample()
        self._pygame.event.pump()
        s = ControlSample(available=True)
        for js, logical, axis, sign in self.map:
            try:
                v = js.get_axis(axis) * sign
            except Exception:
                continue
            if logical == "throttle":
                s.throttle = (v + 1) / 2
            elif logical in ("pitch", "roll", "yaw"):
                setattr(s, logical, v)
        for js, logical, btn in self.btn_map:
            try:
                if logical == "ptt":
                    s.ptt = s.ptt or bool(js.get_button(btn))
            except Exception:
                continue
        return s

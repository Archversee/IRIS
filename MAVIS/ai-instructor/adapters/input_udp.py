"""Receives raw control data from tools/hid_forwarder.py running on the sim PC.
Same interface as HIDInput; used when input.mode: udp."""
from __future__ import annotations
import json
import logging
import socket
import threading
import time

from .input_hid import ControlSample

log = logging.getLogger(__name__)


class UDPInput:
    def __init__(self, port: int = 6947):
        self.port = port
        self._latest = ControlSample()
        self.ok = False

    def connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.port))
        sock.settimeout(2.0)
        threading.Thread(target=self._rx, args=(sock,), daemon=True).start()
        log.info("listening for HID forwarder on udp/%s", self.port)

    def _rx(self, sock):
        while True:
            try:
                data, _ = sock.recvfrom(512)
                d = json.loads(data)
                self._latest = ControlSample(
                    t=d.get("t", time.time()),
                    pitch=d.get("pitch", 0.0), roll=d.get("roll", 0.0),
                    yaw=d.get("yaw", 0.0), throttle=d.get("throttle", 0.0),
                    ptt=bool(d.get("ptt", 0)),
                    available=True)
                self.ok = True
            except socket.timeout:
                if time.time() - self._latest.t > 3:
                    self.ok = False           # forwarder went quiet
            except Exception as e:
                log.debug("udp input: %s", e)

    def poll(self) -> ControlSample:
        return self._latest if self.ok else ControlSample()

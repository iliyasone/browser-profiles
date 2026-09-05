"""Stop browsers nobody is watching.

A running profile is a full Chromium with a virtual display; on a small host a
handful of forgotten ones fill the memory. Every viewer of a screen holds a
WebSocket open to the profile's container, so "someone is watching" is simply
"the container's screen port has an established connection". A profile with
no viewer for `timeout` gets stopped; its cookies stay, and the next start
brings it back on the same identity."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Protocol

from app.store import ProfileSpec

log = logging.getLogger(__name__)

# Linux `/proc/net/tcp` state column for ESTABLISHED.
_ESTABLISHED = "01"


def established_on_port(proc_net_tcp: str, port: int) -> int:
    """Count established connections whose local port is `port` in the text of
    `/proc/net/tcp` (and/or `/proc/net/tcp6`; the two concatenate fine)."""
    suffix = f":{port:04X}"
    count = 0
    for line in proc_net_tcp.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0] == "sl":
            continue
        if fields[1].upper().endswith(suffix) and fields[3] == _ESTABLISHED:
            count += 1
    return count


class Profiles(Protocol):
    def list(self) -> list[ProfileSpec]: ...


class Browsers(Protocol):
    def status(self, name: str) -> str: ...
    def viewers(self, name: str) -> int: ...
    def stop(self, name: str) -> None: ...


class IdleStop:
    """`tick()` looks at every running profile: a viewer resets its clock, no
    viewer for `timeout` stops it. A profile first seen running is given the
    full timeout (it was just started, its viewer is still on the way)."""

    def __init__(
        self,
        profiles: Profiles,
        browsers: Browsers,
        timeout: timedelta,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profiles = profiles
        self.browsers = browsers
        self.timeout = timeout.total_seconds()
        self.clock = clock
        self.last_viewed: dict[str, float] = {}
        # For /healthz: proof the loop is alive and what it last did.
        self.ticks = 0
        self.last_tick: float | None = None
        self.stopped_total = 0
        self.last_error: str | None = None

    def tick(self) -> list[str]:
        """Stop what is idle; returns the names stopped."""
        now = self.clock()
        self.ticks += 1
        self.last_tick = now
        stopped: list[str] = []
        running = {
            spec.name
            for spec in self.profiles.list()
            if self.browsers.status(spec.name) == "running"
        }
        for name in list(self.last_viewed):
            if name not in running:
                del self.last_viewed[name]
        for name in sorted(running):
            if name not in self.last_viewed or self.browsers.viewers(name) > 0:
                self.last_viewed[name] = now
            elif now - self.last_viewed[name] >= self.timeout:
                log.info("stopping %s: no viewer for %.0fs", name, self.timeout)
                self.browsers.stop(name)
                del self.last_viewed[name]
                stopped.append(name)
                self.stopped_total += 1
        return stopped

    def report(self) -> dict[str, object]:
        return {
            "timeout_seconds": self.timeout,
            "ticks": self.ticks,
            "last_tick_seconds_ago": (
                None if self.last_tick is None else round(self.clock() - self.last_tick)
            ),
            "watched": sorted(self.last_viewed),
            "stopped_total": self.stopped_total,
            "last_error": self.last_error,
        }

    def run_forever(self, interval: float, stop: threading.Event) -> None:
        while not stop.wait(interval):
            try:
                self.tick()
            except Exception as error:  # noqa: BLE001 - keep the loop alive, whatever Docker says
                self.last_error = f"{type(error).__name__}: {error}"
                log.exception("idle check failed")

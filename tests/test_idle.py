from datetime import timedelta

from app.idle import IdleStop, established_on_port
from app.store import ProfileSpec

# The four columns that matter of /proc/net/tcp: local, remote, state.
PROC_NET_TCP = "\n".join(
    [
        "  sl  local_address rem_address   st tx_queue rx_queue",
        "   0: 00000000:0BB8 00000000:0000 0A 00000000:00000000",
        "   1: 040015AC:0BB8 0D0015AC:A2DD 01 00000000:00000000",
        "   2: 0100007F:1F90 0100007F:C3A0 01 00000000:00000000",
        "   3: 040015AC:0BB8 0D0015AC:A2E0 06 00000000:00000000",
        "   0: " + "0" * 32 + ":0BB8 " + "0" * 32 + ":0000 0A 00000000:00000000",
    ]
)


def test_counts_established_connections_on_the_screen_port() -> None:
    # The listener (0A), a TIME_WAIT (06) and an internal connection on
    # another port do not count; the one established viewer does.
    assert established_on_port(PROC_NET_TCP, 3000) == 1
    assert established_on_port(PROC_NET_TCP, 8080) == 1
    assert established_on_port("", 3000) == 0


class FakeProfiles:
    def __init__(self, names: list[str]) -> None:
        self.specs = [ProfileSpec(name=n) for n in names]

    def list(self) -> list[ProfileSpec]:
        return self.specs


class FakeBrowsers:
    def __init__(self, running: set[str]) -> None:
        self.running = running
        self.watching: set[str] = set()
        self.stopped: list[str] = []

    def status(self, name: str) -> str:
        return "running" if name in self.running else "stopped"

    def viewers(self, name: str) -> int:
        return 1 if name in self.watching else 0

    def stop(self, name: str) -> None:
        self.running.discard(name)
        self.stopped.append(name)


def test_stops_a_profile_nobody_watched_for_the_timeout() -> None:
    now = 0.0
    browsers = FakeBrowsers({"a", "b"})
    idle = IdleStop(
        FakeProfiles(["a", "b"]), browsers, timedelta(minutes=15), lambda: now
    )
    browsers.watching = {"b"}

    assert idle.tick() == []  # first sight: both get the full timeout
    now = 14 * 60
    assert idle.tick() == []
    now = 15 * 60
    assert idle.tick() == ["a"]  # b is being watched, a is not
    assert browsers.running == {"b"}
    assert idle.report() == {
        "timeout_seconds": 900.0,
        "ticks": 3,
        "last_tick_seconds_ago": 0,
        "watched": ["b"],
        "stopped_total": 1,
        "last_error": None,
    }

    browsers.watching = set()
    now = 29 * 60
    assert idle.tick() == []  # b's clock started when its viewer left
    now = 30 * 60
    assert idle.tick() == ["b"]


def test_a_viewer_resets_the_clock_and_a_restart_gets_a_fresh_one() -> None:
    now = 0.0
    browsers = FakeBrowsers({"a"})
    idle = IdleStop(FakeProfiles(["a"]), browsers, timedelta(minutes=1), lambda: now)
    idle.tick()
    now = 50
    browsers.watching = {"a"}
    idle.tick()
    browsers.watching = set()
    now = 100
    assert idle.tick() == []  # viewed at 50, so idle only since then
    now = 110
    assert idle.tick() == ["a"]

    browsers.running.add("a")  # started again by hand
    now = 111
    assert idle.tick() == []
    now = 170
    assert idle.tick() == []
    now = 171
    assert idle.tick() == ["a"]

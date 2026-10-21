"""Which way each message goes to the robot: its own broker on the home network (local) or Yarbo's cloud.

Local first, the cloud as a fallback, so either path alone is enough:
- the robot drops to 4G (unreachable on the home network) when its HaLow link is weak, and the cloud still reaches it;
- a firmware update could close or encrypt the local broker, and the cloud still works;
- the cloud can be down or signed out, and the local broker still works.

Reads go local while the local broker is connected and answering; after LOCAL_READ_MISSES unanswered local reads in a
row they go through the cloud for LOCAL_READ_BACKOFF_S, then local is tried again. Commands go local while the local
broker is connected, else through the cloud. Controller decides when a command may be repeated on the other route
(only pause, stop and return to dock: repeating a start or a resume could restart a plan). MapWriter takes one route
for a whole write. The route mode (Settings → Robot, `robot_route`): auto, local only, or cloud only.

Only Controller and MapWriter send commands, through `command()`; nothing else reaches LocalTransport.command or
CloudTransport.publish (tests/test_guards.py). cmd_vel is refused on both routes.
"""
import time

from .transport_local import check_command, check_read_only

ROUTE_MODES = ("auto", "local", "cloud")
# Map, settings, plan and backup writes on the local broker are not proved yet (run commands and a setting are,
# 2026-10-21). Until they are, Automatic sends writes through the cloud while it is up and locally only without it.
LOCAL_WRITES_PROVED = False
LOCAL_READ_MISSES = 2
LOCAL_READ_BACKOFF_S = 300


class NoRoute(ConnectionError):
    """Neither route can reach the robot (or the route mode rules out the one that could)."""


class RobotLink:
    def __init__(self, rec, cloud, store):
        self.rec, self.cloud, self.store = rec, cloud, store
        self._local_misses = 0
        self._local_reads_off_until = 0.0
        self.reads_route: str | None = None
        self.commands_route: str | None = None
        self.last_command: dict | None = None
        self._was = None
        self.on_change = None            # (what, before, after) -> None, for an event when a route changes

    # -- which way -------------------------------------------------------------------------------------------------
    def mode(self) -> str:
        m = self.store.setting("robot_route") or "auto"
        return m if m in ROUTE_MODES else "auto"

    def local_up(self) -> bool:
        t = getattr(self.rec, "transport", None)
        return bool(t and t.connected and hasattr(t, "command"))

    def cloud_up(self) -> bool:
        c = self.cloud
        return bool(c and c.state == "connected" and getattr(c, "connected", True))

    def read_route(self) -> str | None:
        m, now = self.mode(), time.time()
        local = self.local_up() and (m == "local" or now >= self._local_reads_off_until)
        if m == "local":
            return "local" if self.local_up() else None
        if m == "cloud":
            return "cloud" if self.cloud_up() else None
        return "local" if local else "cloud" if self.cloud_up() else "local" if self.local_up() else None

    def command_route(self) -> str | None:
        m = self.mode()
        if m == "local":
            return "local" if self.local_up() else None
        if m == "cloud":
            return "cloud" if self.cloud_up() else None
        return "local" if self.local_up() else "cloud" if self.cloud_up() else None

    def write_route(self) -> str | None:
        """The route a map or settings write starts on (it keeps it for the whole write)."""
        if self.mode() == "auto" and not LOCAL_WRITES_PROVED and self.cloud_up():
            return "cloud"
        return self.command_route()

    def other_route(self, route: str) -> str | None:
        """The fallback for a command that went `route` and was not confirmed (auto mode only)."""
        if self.mode() != "auto":
            return None
        if route == "local":
            return "cloud" if self.cloud_up() else None
        return "local" if self.local_up() else None

    @property
    def state(self) -> str:
        """For the interlocks: "connected" while some route reaches the robot."""
        return "connected" if self.command_route() else "no route to the robot"

    # -- sending ---------------------------------------------------------------------------------------------------
    def read(self, cmd: str, payload: dict | None = None) -> str:
        check_read_only(cmd)
        route = self.read_route()
        if route is None:
            raise NoRoute(self._why_none())
        if route == "local":
            self.rec.transport.request(cmd, payload)
        else:
            self.cloud.publish(cmd, payload or {})
        self._note("reads", route)
        return route

    def note_read(self, route: str | None, answered: bool) -> None:
        """A read's outcome. Unanswered local reads in a row move reads to the cloud for a while."""
        if route != "local":
            return
        if answered:
            self._local_misses = 0
            return
        self._local_misses += 1
        if self._local_misses >= LOCAL_READ_MISSES and self.cloud_up() and self.mode() == "auto":
            self._local_reads_off_until = time.time() + LOCAL_READ_BACKOFF_S
            self._local_misses = 0

    def command(self, cmd: str, payload: dict, route: str | None = None) -> str:
        """Send a command (Controller and MapWriter only). Returns the route it went."""
        check_command(cmd)
        route = route or self.command_route()
        if route is None:
            raise NoRoute(self._why_none())
        if route == "local":
            if not self.local_up():
                raise NoRoute("the robot's local broker is not connected")
            self.rec.transport.command(cmd, payload)
        else:
            if not self.cloud_up():
                raise NoRoute(f"the Yarbo cloud is {getattr(self.cloud, 'state', 'not set up')}")
            self.cloud.publish(cmd, payload)
        self.last_command = {"cmd": cmd, "route": route, "at": time.time()}
        self._note("commands", route)
        return route

    def _why_none(self) -> str:
        m = self.mode()
        if m == "local":
            return "the robot's local broker is not connected (route: local only)"
        if m == "cloud":
            return f"the Yarbo cloud is {getattr(self.cloud, 'state', 'not set up')} (route: cloud only)"
        return "neither the robot's local broker nor the Yarbo cloud is connected"

    def _note(self, what: str, route: str) -> None:
        before = getattr(self, f"{what}_route")
        setattr(self, f"{what}_route", route)
        if before and before != route and self.on_change:
            self.on_change(what, before, route)

    def status(self) -> dict:
        return {"mode": self.mode(), "local": self.local_up(), "cloud": self.cloud_up(),
                "reads": self.read_route(), "commands": self.command_route(),
                "fallback_ready": self.mode() == "auto" and self.local_up() and self.cloud_up(),
                "local_reads_paused_s": max(0, round(self._local_reads_off_until - time.time())) or None,
                "last_command": self.last_command}

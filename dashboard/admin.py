"""The admin page (the dashboard at / and /admin) and the demo backend for its Dashboard tab.

The robot brain answers GET /admin/overview and GET /usage from its live modules. Here they come from
data/demo_dashboard.json, in the same shapes, with "demo": true so the page shows a DEMO DATA banner. Times in the
file are given as seconds ago (keys ending in _ago_s) and turned into timestamps on every request, so the demo
always looks current. When a robot (or tools/fake_robot.py) is connected to the sensor hub, the Robot card shows
that live link instead of the demo values.

Routes:
  GET  /, /admin          the admin page
  GET  /admin/overview    robot, uptime, models, tracker, notify, tools, pending, errors
  GET  /usage             spend today, last 7 days and in total, per model
  GET  /admin/pending     {"pending": [...]}
  POST /agent/approve     {"id", "approve"}: approve or cancel a demo pending call (nothing runs)
  POST /track/follow      {"enabled"}: flip the demo tracker's follow flag
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Callable

from aiohttp import web

from .hub import SensorHub

PAGE = Path(__file__).with_name("admin.html")
DEMO = Path(__file__).resolve().parent.parent / "data" / "demo_dashboard.json"


def _stamp(obj: Any, now: float) -> Any:
    """Replace every "<key>_ago_s": n with "<key>": now - n (recursively)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.endswith("_ago_s") and k not in ("seen_s_ago", "face_seen_s_ago"):
                out[k[: -len("_ago_s")]] = None if v is None else round(now - v, 3)
            else:
                out[k] = _stamp(v, now)
        return out
    if isinstance(obj, list):
        return [_stamp(v, now) for v in obj]
    return obj


class DemoAdmin:
    def __init__(self, hub: SensorHub | None = None, path: Path = DEMO, clock: Callable[[], float] = time.time) -> None:
        self.hub = hub
        self.clock = clock
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.label: str = raw.get("label", "demo data")
        self.note: str = raw.get("about", "")
        self._overview: dict = raw["overview"]
        self._usage: dict = raw["usage"]
        self.pending: list[dict] = copy.deepcopy(raw.get("pending", []))
        self.follow: bool = bool(raw["overview"].get("tracker", {}).get("follow", True))
        self.started = clock() - float(raw["overview"].get("uptime_s", 0))

    # ------------------------------------------------------------------ data
    def robot(self, now: float) -> dict:
        """The live hub link when a robot is connected, else the demo robot."""
        hub = self.hub
        if hub is not None and hub.robot is not None:
            st = hub.status()
            info = st.get("robot_info") or {}
            age = st.get("latest_age_s")
            return {
                "seen_s_ago": age if age is not None else 0.0,
                "devices": {info.get("device") or "robot": {"app_version": info.get("firmware") or None,
                                                            "last_ota": None, "board": None}},
                "conversations": [], "live": True,
            }
        return {**_stamp(copy.deepcopy(self._overview["robot"]), now), "live": False}

    def overview(self) -> dict:
        now = self.clock()
        out = _stamp(copy.deepcopy(self._overview), now)
        tracker = out.get("tracker")
        if isinstance(tracker, dict):
            tracker["follow"] = self.follow
        return {**out, "now": now, "uptime_s": int(now - self.started), "robot": self.robot(now),
                "pending": _stamp(copy.deepcopy(self.pending), now), "frame": False, "demo": True,
                "demo_note": self.note}

    def usage(self) -> dict:
        today = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        u = copy.deepcopy(self._usage)
        u["today"]["date"] = today
        return {**u, "demo": True}

    # ------------------------------------------------------------------ routes
    def routes(self, app: web.Application) -> None:
        async def page(_: web.Request) -> web.Response:
            return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html",
                                headers={"Cache-Control": "no-store"})

        async def overview(_: web.Request) -> web.Response:
            return web.json_response(self.overview())

        async def usage(_: web.Request) -> web.Response:
            return web.json_response(self.usage())

        async def pending(_: web.Request) -> web.Response:
            return web.json_response({"pending": _stamp(copy.deepcopy(self.pending), self.clock())})

        async def body(req: web.Request) -> dict:
            try:
                b = await req.json()
            except ValueError:
                return {}
            return b if isinstance(b, dict) else {}

        async def approve(req: web.Request) -> web.Response:
            b = await body(req)
            pid = b.get("id")
            hit = next((p for p in self.pending if p.get("id") == pid), None)
            if hit is None:
                return web.json_response({"error": "not pending (or already handled)"}, status=404)
            self.pending.remove(hit)
            approved = bool(b.get("approve"))
            return web.json_response({"ok": True, "approved": approved, "tool": hit.get("tool"), "demo": True,
                                      "result": {"note": "demo: nothing ran"} if approved else None})

        async def follow(req: web.Request) -> web.Response:
            b = await body(req)
            self.follow = bool(b.get("enabled", not self.follow))
            return web.json_response({"ok": True, "follow": self.follow, "demo": True})

        app.router.add_get("/", page)
        app.router.add_get("/admin", page)
        app.router.add_get("/admin/", page)
        app.router.add_get("/admin/overview", overview)
        app.router.add_get("/admin/pending", pending)
        app.router.add_get("/usage", usage)
        app.router.add_post("/agent/approve", approve)
        app.router.add_post("/track/follow", follow)

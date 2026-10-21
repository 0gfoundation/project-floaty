"""Run the dashboard on its own: python -m dashboard.server [--host 0.0.0.0] [--port 8175] [--tabs dashboard,memory,sensors]

Open http://<this machine>:<port>/ (also /admin) for the admin page with its tab bar; /#memory opens a tab directly.
The Sensors tab is also its own page at /sensors. A robot (or tools/fake_robot.py) connects to /robot/telemetry.
GET /link answers {"telemetry": true|false} so robot firmware can poll whether anyone is watching.
GET /api/tabs lists the tab bar: every tab, with "enabled" true for the ones this server serves.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from aiohttp import web

from . import config, faces
from .admin import DemoAdmin
from .hub import SensorHub
from .memory import DEMO, MemoryStore
from .zg import ZeroG, load_dotenv

STATIC = Path(__file__).with_name("static")


def make_app(hub: SensorHub | None = None, tabs: tuple[str, ...] | None = None,
             memory_path: Path | None = None, dashboard_path: Path | None = None) -> web.Application:
    enabled = config.ENABLED if tabs is None else tabs
    hub = hub or SensorHub()
    app = web.Application()

    # The page itself and the sensor hub are always served: /sensors and the robot link keep working even when
    # their tab is switched off. Each other backend comes up only with its tab.
    admin = DemoAdmin(hub, dashboard_path) if dashboard_path else DemoAdmin(hub)
    admin.routes(app)
    hub.routes(app)
    available: set[str] = {"overview", "sensors"}

    if "memory" in enabled:
        MemoryStore(memory_path or DEMO, zg=ZeroG()).routes(app)
        available.add("memory")
    if "faces" in enabled:
        faces.routes(app)
        available.add("faces")

    async def link(_: web.Request) -> web.Response:
        return web.json_response(hub.link_fields())

    async def tab_list(_: web.Request) -> web.Response:
        return web.json_response({"tabs": config.tab_list(enabled, available)})

    app.router.add_get("/link", link)
    app.router.add_get("/api/tabs", tab_list)
    app.router.add_static("/static/", STATIC)
    return app


def main() -> None:
    load_dotenv()  # ZG_API_KEY and friends for "Ask Floaty"; real environment variables win
    p = argparse.ArgumentParser(description="Live dashboard for a desk bot")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8175)
    p.add_argument("--tabs", default=None,
                   help=f"comma separated tabs to switch on, or 'all' (default: {','.join(config.ENABLED)}; "
                        f"known: {','.join(config.TAB_IDS)}; 'dashboard' means overview)")
    p.add_argument("--memory", type=Path, default=None, help="memory JSON for the Memory tab (default: demo data)")
    p.add_argument("--dashboard", type=Path, default=None, help="JSON for the Dashboard tab (default: demo data)")
    a = p.parse_args()
    try:
        tabs = config.parse_tabs(a.tabs)
    except ValueError as e:
        p.error(str(e))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    web.run_app(make_app(tabs=tabs, memory_path=a.memory, dashboard_path=a.dashboard), host=a.host, port=a.port)


if __name__ == "__main__":
    main()

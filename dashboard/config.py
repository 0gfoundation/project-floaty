"""Which dashboard tabs exist, and which are switched on.

Every tab the admin page has is listed in TABS, in the order the tab bar shows them. A tab whose id is in ENABLED
and whose backend this server has is live; the rest show as disabled "later" chips (or stay out of the bar when
listed is False). To ship a tab: give it a backend in dashboard/server.py, then add its id to ENABLED (or pass it
in --tabs). The page and its code are already in dashboard/admin.html.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Tab:
    id: str  # the section id in admin.html, also the URL hash (/#memory)
    label: str
    blurb: str  # one line for the tooltip
    listed: bool = True  # show a disabled "later" chip while the tab is off


TABS: tuple[Tab, ...] = (
    Tab("overview", "Dashboard", "Robot link, usage today, face tracker, tool calls waiting for a yes, models, errors"),
    Tab("memory", "Memory", "Facts, documents and past conversations the robot remembers"),
    Tab("sensors", "Sensors", "Live sensors and hearing: mic, motion, proximity, touch, servos, power"),
    Tab("tools", "Tools", "Test-run any tool the agent can call, with an audit log"),
    Tab("tables", "Tables", "Tables the robot keeps for you, with read-only SQL"),
    Tab("crons", "Crons", "Routines the robot runs on a schedule"),
    Tab("notify", "Notifications", "The notification bell, sleep override and history"),
    Tab("hooks", "Webhooks", "Outside events that can reach the robot"),
    Tab("wake", "Wake", "Wake word checks, enrollment and labelling"),
    Tab("router", "Router", "Shadow router agreement and label review"),
    Tab("faces", "Faces", "Every expression and gesture with its head moves, previewed without a robot", listed=False),
)

# Tabs switched on today. Flip a tab on by adding its id here.
ENABLED: tuple[str, ...] = ("overview", "memory", "sensors")

TAB_IDS = tuple(t.id for t in TABS)
ALIASES = {"dashboard": "overview", "senses": "sensors", "notifications": "notify", "webhooks": "hooks"}


def parse_tabs(spec: str | None) -> tuple[str, ...]:
    """Turn a --tabs value ("dashboard,memory" or "all") into enabled tab ids, in TABS order."""
    if spec is None:
        return ENABLED
    spec = spec.strip()
    if spec == "all":
        wanted = set(TAB_IDS)
    else:
        wanted = {ALIASES.get(s.strip().lower(), s.strip().lower()) for s in spec.split(",") if s.strip()}
    unknown = wanted - set(TAB_IDS)
    if unknown:
        raise ValueError(f"unknown tab(s): {', '.join(sorted(unknown))}; known: {', '.join(TAB_IDS)}")
    return tuple(t for t in TAB_IDS if t in wanted)


def tab_list(enabled: tuple[str, ...], available: set[str]) -> list[dict]:
    """The tab bar as JSON. A tab is live only when it is switched on and this server has its backend."""
    return [dict(asdict(t), enabled=t.id in enabled and t.id in available) for t in TABS]

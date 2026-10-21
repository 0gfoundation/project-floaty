"""Admin page, tab config, and the Dashboard, Faces and Memory routes."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dashboard import config, faces
from dashboard.hub import SensorHub
from dashboard.memory import DEMO, MemoryStore
from dashboard.server import make_app


def with_client(fn, **kw):
    async def run():
        c = TestClient(TestServer(make_app(**kw)))
        await c.start_server()
        try:
            await fn(c)
        finally:
            await c.close()
    asyncio.run(run())


# ---------------------------------------------------------------- page and tabs

def test_admin_page_and_direct_pages():
    async def check(c):
        for path in ("/", "/admin"):
            r = await c.get(path)
            text = await r.text()
            assert r.status == 200 and "Floaty <span>Admin</span>" in text
            assert "Jarvis" not in text
        r = await c.get("/sensors")
        assert r.status == 200 and "ROBOT SENSES" in await r.text()
        assert (await c.get("/link")).status == 200
    with_client(check)


def test_tab_list_default():
    async def check(c):
        tabs = (await (await c.get("/api/tabs")).json())["tabs"]
        assert [t["id"] for t in tabs] == list(config.TAB_IDS)
        on = [t["id"] for t in tabs if t["enabled"]]
        assert on == ["overview", "memory", "sensors"]
        labels = {t["id"]: t["label"] for t in tabs}
        assert labels["overview"] == "Dashboard"
        for t in tabs:
            assert {"id", "label", "blurb", "listed", "enabled"} <= set(t)
        later = {t["id"] for t in tabs if not t["enabled"] and t["listed"]}
        assert later == {"tools", "tables", "crons", "notify", "hooks", "wake", "router"}
        faces_tab = next(t for t in tabs if t["id"] == "faces")
        assert not faces_tab["enabled"] and not faces_tab["listed"]
        assert (await c.get("/api/faces")).status == 404  # Faces is off today
    with_client(check)


def test_tabs_flag():
    async def check(c):
        tabs = {t["id"]: t["enabled"] for t in (await (await c.get("/api/tabs")).json())["tabs"]}
        assert tabs["overview"] and tabs["faces"] and not tabs["memory"] and not tabs["sensors"]
        assert (await c.get("/memory/facts")).status == 404
        assert (await c.get("/api/faces")).status == 200
        assert (await c.get("/faces")).status == 200
        assert (await c.get("/sensors")).status == 200  # the direct URL always works
    with_client(check, tabs=config.parse_tabs("dashboard,faces"))


def test_parse_tabs():
    assert config.parse_tabs(None) == config.ENABLED
    assert config.parse_tabs("Sensors, memory, dashboard") == ("overview", "memory", "sensors")  # TABS order
    assert config.parse_tabs("senses,notifications") == ("sensors", "notify")  # aliases
    assert config.parse_tabs("all") == config.TAB_IDS
    with pytest.raises(ValueError):
        config.parse_tabs("memory,bogus")


def test_tab_without_a_backend_stays_later():
    tabs = {t["id"]: t["enabled"] for t in config.tab_list(("overview", "tools"), {"overview"})}
    assert tabs["overview"] and not tabs["tools"]


# ---------------------------------------------------------------- dashboard (overview)

def test_overview_demo_shape():
    async def check(c):
        o = await (await c.get("/admin/overview")).json()
        assert o["demo"] is True and o["demo_note"] and o["frame"] is False
        assert {"now", "uptime_s", "llm", "robot", "tracker", "notify", "tools", "pending", "pending_embeddings", "errors"} <= set(o)
        r = o["robot"]
        assert r["live"] is False and r["seen_s_ago"] is not None and isinstance(r["conversations"], list)
        dev = next(iter(r["devices"].values()))
        assert dev["app_version"] and abs(dev["last_ota"] - (o["now"] - 5400)) < 5
        prov = o["llm"]["providers"]
        assert prov and all({"configured", "model", "healthy", "calls", "last_ok", "last_error"} <= set(p) for p in prov.values())
        assert all(p["model"].startswith("demo-") for p in prov.values())
        assert o["llm"]["chains"] and o["llm"]["embed_model"]
        t = o["tracker"]
        assert {"enabled", "mode", "follow", "frames", "faces", "pose", "face_seen_s_ago", "moves"} <= set(t)
        assert {"card", "wake", "sleep", "force_sleep", "busy"} <= set(o["notify"])
        p = o["pending"][0]
        assert {"id", "tool", "args", "source", "ts"} <= set(p) and p["ts"] < o["now"]
        u = await (await c.get("/usage")).json()
        assert u["demo"] is True and u["today"]["usd"] > 0 and u["today"]["models"]
        assert all({"calls", "usd"} <= set(v) for v in u["today"]["models"].values())
        assert u["last_7_days"]["usd"] >= u["today"]["usd"] and u["total"]["usd"] >= u["last_7_days"]["usd"]
    with_client(check)


def test_overview_actions():
    async def check(c):
        pid = (await (await c.get("/admin/pending")).json())["pending"][0]["id"]
        r = await (await c.post("/agent/approve", json={"id": pid, "approve": True})).json()
        assert r["ok"] and r["approved"] and r["demo"]
        assert (await (await c.get("/admin/pending")).json())["pending"] == []
        assert (await c.post("/agent/approve", json={"id": pid, "approve": False})).status == 404
        before = (await (await c.get("/admin/overview")).json())["tracker"]["follow"]
        r = await (await c.post("/track/follow", json={"enabled": not before})).json()
        assert r["follow"] is (not before)
        assert (await (await c.get("/admin/overview")).json())["tracker"]["follow"] is (not before)
    with_client(check)


def test_overview_shows_a_connected_robot_live():
    hub = SensorHub()

    async def check(c):
        async with c.ws_connect("/robot/telemetry", headers={"X-Device": "test-bot", "X-Firmware": "9.9.9"}) as ws:
            await ws.send_str(json.dumps({"t": "frame", "imu": {"ax": 0}}))
            for _ in range(50):
                if hub.robot is not None:
                    break
                await asyncio.sleep(0.02)
            r = (await (await c.get("/admin/overview")).json())["robot"]
            assert r["live"] is True and r["devices"] == {"test-bot": {"app_version": "9.9.9", "last_ota": None, "board": None}}
        for _ in range(50):
            if hub.robot is None:
                break
            await asyncio.sleep(0.02)
        assert (await (await c.get("/admin/overview")).json())["robot"]["live"] is False
    with_client(check, hub=hub)


# ---------------------------------------------------------------- faces

def test_faces_api():
    async def check(c):
        d = await (await c.get("/api/faces")).json()
        assert len(d["expressions"]) == 16 and len(d["gestures"]) == 7
        names = {e["name"] for e in d["expressions"]}
        assert {"idle", "happy", "sad", "love", "silent"} <= names
        for e in d["expressions"]:
            assert e["description"] and e["moves"]
            assert all(len(m) == 4 for m in e["moves"])
            assert e["duration_ms"] == sum(m[3] for m in e["moves"])
            assert e["image"] == f"/static/faces/{e['name']}.png"
        by = {e["name"]: e for e in d["expressions"]}
        assert set(by["love"]["moods"]) == {"loving", "kissy", "embarrassed"}
        assert by["listening"]["moods"] == []
        assert all(d["moods"][w] in names for w in d["moods"])
        g = {x["name"]: x for x in d["gestures"]}
        assert set(g) == {"nod", "shake", "tilt", "bounce", "wiggle", "look_around", "bow"}
        assert g["bounce"]["moves"] == by["excited"]["moves"] and g["bounce"]["same_as"] == "excited"
        img = await c.get("/static/faces/happy.png")
        assert img.status == 200 and (await img.read())[:4] == b"\x89PNG"
    with_client(check, tabs=config.parse_tabs("faces"))


def test_every_expression_has_an_image_file():
    assert all(faces.has_image(n) for n in faces.EXPRESSIONS)


# ---------------------------------------------------------------- memory

def test_memory_shapes():
    async def check(c):
        facts = (await (await c.get("/memory/facts")).json())["facts"]
        assert facts and set(facts[0]) == {"id", "text", "kind", "entity", "created", "expires", "source"}
        every = (await (await c.get("/memory/facts?all=1")).json())["facts"]
        now = datetime.now().astimezone()
        expired = [f for f in every if f["expires"] and datetime.fromisoformat(f["expires"]) <= now]
        assert expired and len(every) == len(facts) + len(expired)
        created = [datetime.fromisoformat(f["created"]) for f in facts]
        assert created == sorted(created, reverse=True)

        docs = (await (await c.get("/memory/docs")).json())["documents"]
        assert docs and {"id", "title", "source", "url", "created", "chars", "chunks", "unembedded"} <= set(docs[0])
        assert all(x["chunks"] >= 1 for x in docs)

        eps = (await (await c.get("/memory/episodes?limit=2")).json())["episodes"]
        assert len(eps) == 2
        assert {"id", "started", "ended", "status", "summary", "open_loops", "new_facts", "model", "turns"} <= set(eps[0])
        assert isinstance(eps[0]["turns"], int)
        one = await (await c.get(f"/memory/episodes/{eps[1]['id']}")).json()
        assert one["id"] == eps[1]["id"] and one["turns"] and {"ts", "role", "text"} == set(one["turns"][0])
        assert (await c.get("/memory/episodes/nope")).status == 404

        brief = await (await c.get("/memory/brief")).json()
        assert "## Things to remember" in brief["brief"] and brief["pending_embeddings"] == 0

        meta = await (await c.get("/memory/meta")).json()
        assert meta["label"] == "demo data" and meta["read_only"] is True
    with_client(check)


def test_memory_recall():
    async def check(c):
        r = await (await c.post("/memory/recall", json={"query": "water the fern", "k": 8})).json()
        hits = r["results"]
        assert hits and len(hits) <= 8
        assert {h["source"] for h in hits} >= {"fact", "doc", "episode"}
        assert hits[0]["score"] == max(h["score"] for h in hits)
        assert all("fern" in h["text"].lower() or "water" in h["text"].lower() for h in hits)
        doc = next(h for h in hits if h["source"] == "doc")
        assert {"doc_id", "title", "url", "chunk"} <= set(doc)
        # expired facts never come back from recall
        r = await (await c.post("/memory/recall", json={"query": "vet"})).json()
        assert not any("vet" in h["text"] for h in r["results"] if h["source"] == "fact")
        assert (await (await c.post("/memory/recall", json={"query": ""})).json())["results"] == []
        assert (await c.post("/memory/recall", json={"query": 3})).status == 400
        assert (await c.post("/memory/recall", json={"query": "tea", "k": "x"})).status == 400
    with_client(check)


def test_memory_custom_file(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"label": "my file", "facts": [{"id": 1, "text": "Test fact", "kind": "fact",
                                                             "created": "2026-01-01T00:00+00:00", "source": "voice"}]}))
    s = MemoryStore(p)
    assert s.facts()[0]["text"] == "Test fact" and s.documents() == [] and s.episodes() == []

    async def check(c):
        assert (await (await c.get("/memory/meta")).json())["label"] == "my file"
    with_client(check, memory_path=p)


def test_demo_data_is_labelled_fictional():
    raw = json.loads(DEMO.read_text())
    assert raw["label"] == "demo data"
    assert "fictional" in raw["about"].lower() or "made-up" in raw["about"].lower()


def test_dashboard_demo_data_is_labelled_fictional():
    from dashboard.admin import DEMO as DASH
    raw = json.loads(DASH.read_text())
    assert raw["label"] == "demo data" and "made-up" in raw["about"].lower()

"""Dashboard hub: link field, robot to viewer relay, audio, live transcripts."""
from __future__ import annotations

import asyncio
import json
import math
import struct

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from dashboard.hub import LINGER_S, SensorHub, Utterances


class Stt:
    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, pcm: bytes) -> dict:
        self.calls += 1
        return {"text": f"hello {self.calls}", "ms": 3}


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def tone(seconds: float, amp: int = 8000) -> bytes:
    n = int(16000 * seconds)
    return struct.pack(f"<{n}h", *(int(amp * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(n)))


def quiet(seconds: float) -> bytes:
    return b"\x02\x00" * int(16000 * seconds)


async def client(hub: SensorHub) -> TestClient:
    app = web.Application()
    hub.routes(app)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


async def next_json(ws, t: str):
    while True:
        msg = await asyncio.wait_for(ws.receive(), 2)
        if msg.type == WSMsgType.TEXT:
            obj = json.loads(msg.data)
            if obj.get("t") == t:
                return obj


def test_link_field_follows_viewers():
    async def run():
        clock = Clock()
        hub = SensorHub(clock=clock)
        assert hub.link_fields() == {"telemetry": False}
        c = await client(hub)
        try:
            v = await c.ws_connect("/sensors/ws")
            await next_json(v, "hello")
            assert hub.link_fields() == {"telemetry": True}
            await v.close()
            await asyncio.sleep(0.05)
            assert hub.link_fields() == {"telemetry": True}  # lingers
            clock.t += LINGER_S + 1
            assert hub.link_fields() == {"telemetry": False}
        finally:
            await c.close()

    asyncio.run(run())


def test_robot_frames_reach_viewer_and_config_follows_audio_toggle():
    async def run():
        hub = SensorHub()
        c = await client(hub)
        try:
            v = await c.ws_connect("/sensors/ws")
            await next_json(v, "hello")
            r = await c.ws_connect("/robot/telemetry", headers={"X-Device": "aa:bb", "X-Firmware": "2.6.0"})
            cfg = await next_json(r, "config")
            assert cfg == {"t": "config", "sensors_hz": 10, "audio": True}
            up = await next_json(v, "robot")
            assert up["connected"] and up["firmware"] == "2.6.0"
            await r.send_str(json.dumps({"t": "sensors", "mic": {"db": -40}, "prox": {"ps": 12}}))
            s = await next_json(v, "sensors")
            assert s["prox"]["ps"] == 12
            await r.send_str(json.dumps({"t": "event", "name": "tap"}))
            assert (await next_json(v, "event"))["name"] == "tap"
            await r.send_bytes(quiet(0.02))
            while True:
                msg = await asyncio.wait_for(v.receive(), 2)
                if msg.type == WSMsgType.BINARY:
                    assert msg.data[0] == 0 and len(msg.data) == 1 + 640
                    break
            await v.send_str(json.dumps({"t": "audio", "on": False}))
            assert (await next_json(r, "config"))["audio"] is False
            assert hub.status()["counts"]["frames"] == 1
            await r.close()
            await v.close()
        finally:
            await c.close()

    asyncio.run(run())


def test_session_audio_wins_over_robot_audio():
    async def run():
        hub = SensorHub()
        got = []
        hub.viewers[object()] = {"q": asyncio.Queue(10), "audio": True}
        hub.session_audio(quiet(0.02))
        q = next(iter(hub.viewers.values()))["q"]
        got.append(q.get_nowait())
        assert got[0][0] == 1

    asyncio.run(run())


def test_utterances_partial_and_final():
    async def run():
        stt, out = Stt(), []
        u = Utterances(stt, out.append)
        u.feed(quiet(1.0), "robot")
        u.feed(tone(1.6), "robot")
        await asyncio.sleep(0.05)
        u.feed(quiet(1.0), "robot")
        await asyncio.sleep(0.05)
        heard = [o for o in out if o["t"] == "heard"]
        assert any(not o["final"] for o in heard)
        final = [o for o in heard if o["final"]]
        assert len(final) == 1 and final[0]["text"].startswith("hello") and final[0]["dur_ms"] >= 1600

    asyncio.run(run())


def test_short_click_is_dropped():
    async def run():
        out = []
        u = Utterances(Stt(), out.append)
        u.feed(quiet(1.0) + tone(0.1) + quiet(1.0), "robot")
        await asyncio.sleep(0.05)
        assert all(o.get("drop") for o in out)

    asyncio.run(run())

"""Ask Floaty: memory recall plus one chat call to 0G PC (a fake router stands in here)."""
import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dashboard.memory import MemoryStore
from dashboard.zg import ZeroG


async def _client(store: MemoryStore) -> TestClient:
    app = web.Application()
    store.routes(app)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


def test_ask_without_key_explains_setup():
    async def run():
        c = await _client(MemoryStore(zg=ZeroG(env={})))
        try:
            meta = await (await c.get("/memory/meta")).json()
            assert meta["ask"]["configured"] is False
            r = await (await c.post("/memory/ask", json={"question": "what do I like?"})).json()
            assert r["ok"] is False and r["setup"] is True and "ZG_API_KEY" in r["error"]
        finally:
            await c.close()

    asyncio.run(run())


def test_ask_sends_private_trust_mode_and_returns_answer():
    seen = {}

    async def chat(req: web.Request) -> web.Response:
        seen["auth"] = req.headers.get("Authorization")
        seen["trust"] = req.headers.get("X-0G-Provider-Trust-Mode")
        seen["body"] = await req.json()
        return web.json_response({"model": "0gm-1.0-35b-a3b", "choices": [{"message": {"content": " You like filter coffee. "}}],
                                  "x_0g_trace": {"provider": "0xabc", "tee_verified": None}})

    async def run():
        router = web.Application()
        router.router.add_post("/v1/chat/completions", chat)
        rs = TestServer(router)
        await rs.start_server()
        zg = ZeroG(env={"ZG_API_KEY": "test-key", "ZG_BASE_URL": str(rs.make_url("/v1"))})
        c = await _client(MemoryStore(zg=zg))
        try:
            r = await (await c.post("/memory/ask", json={"question": "what coffee do I like"})).json()
            assert r["ok"] and r["answer"] == "You like filter coffee." and r["provider"] == "0xabc"
            assert r["trust_mode"] == "private" and seen["trust"] == "private"
            assert seen["auth"] == "Bearer test-key" and seen["body"]["model"] == "0gm-1.0-35b-a3b"
            assert seen["body"]["reasoning_effort"] == "none"
            assert seen["body"]["messages"][0]["role"] == "system"
        finally:
            await c.close()
            await rs.close()

    asyncio.run(run())


def test_private_mode_503_is_reported_not_retried_elsewhere():
    calls = {"n": 0}

    async def chat(_: web.Request) -> web.Response:
        calls["n"] += 1
        return web.json_response({"error": "no_provider_for_trust_mode"}, status=503)

    async def run():
        router = web.Application()
        router.router.add_post("/v1/chat/completions", chat)
        rs = TestServer(router)
        await rs.start_server()
        zg = ZeroG(env={"ZG_API_KEY": "k", "ZG_BASE_URL": str(rs.make_url("/v1"))})
        c = await _client(MemoryStore(zg=zg))
        try:
            r = await (await c.post("/memory/ask", json={"question": "hello"})).json()
            assert r["ok"] is False and r["status"] == 503 and "fails closed" in r["error"] and calls["n"] == 1
        finally:
            await c.close()
            await rs.close()

    asyncio.run(run())

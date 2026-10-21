"""0G Private Computer client: one OpenAI-compatible chat call, with the trust tier made explicit.

Settings come from the environment (or a .env file next to where you run the server; never commit it):

    ZG_API_KEY      your key from https://pc.0g.ai (card or crypto top-up); without it, Ask stays off
    ZG_BASE_URL     default https://router-api.0g.ai/v1
    ZG_MODEL        default 0gm-1.0-35b-a3b, 0G's own model (runs in TEE enclaves, so private mode works;
                    about 2 s per answer, against 13 to 19 s for glm-5.3, measured 2026-10-21)
    ZG_TRUST_MODE   private (default): the model runs inside an Intel TDX enclave and 0G and the GPU host
                    cannot see the prompt; the router fails closed (HTTP 503) rather than fall back to a lower
                    tier. "verified" or "standard" relax that. Empty sends no trust header.
"""

from __future__ import annotations

import os
import time

import aiohttp

DEFAULT_BASE = "https://router-api.0g.ai/v1"
DEFAULT_MODEL = "0gm-1.0-35b-a3b"  # 0G's own model: about 2 s per answer in private mode
# Reasoning models that think before answering unless told not to; a short spoken answer does not need it
NO_THINKING = ("0gm-",)
TIMEOUT_S = 45.0


class ZeroGError(Exception):
    def __init__(self, msg: str, status: int | None = None) -> None:
        super().__init__(msg)
        self.status = status


class ZeroG:
    def __init__(self, env: dict | None = None) -> None:
        e = os.environ if env is None else env
        self.key = (e.get("ZG_API_KEY") or "").strip()
        self.base = (e.get("ZG_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.model = e.get("ZG_MODEL") or DEFAULT_MODEL
        tm = e.get("ZG_TRUST_MODE")
        self.trust = "private" if tm is None else tm.strip()

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def info(self) -> dict:
        return {"configured": self.configured, "model": self.model, "trust_mode": self.trust or None,
                "service": "0G Private Computer", "base_url": self.base}

    async def chat(self, messages: list[dict], max_tokens: int = 300) -> dict:
        if not self.configured:
            raise ZeroGError("Set ZG_API_KEY to ask Floaty through 0G Private Computer (get a key at pc.0g.ai).")
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        if self.trust:
            headers["X-0G-Provider-Trust-Mode"] = self.trust
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3}
        if self.model.startswith(NO_THINKING):
            body["reasoning_effort"] = "none"  # otherwise its hidden thinking uses up max_tokens and the answer is empty
        t0 = time.monotonic()
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_S, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            for attempt in (1, 2):
                async with s.post(f"{self.base}/chat/completions", json=body, headers=headers) as r:
                    text = await r.text()
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        break
                    if r.status == 503 and self.trust == "private":
                        raise ZeroGError(f"No {self.model} enclave free right now (private mode fails closed rather "
                                         "than fall back). Try again shortly, or pick another TEE model.", 503)
                    if r.status >= 500 and attempt == 1:
                        continue
                    raise ZeroGError(f"0G router answered HTTP {r.status}: {text[:200]}", r.status)
        try:
            answer = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ZeroGError(f"Unexpected reply from the router: {str(data)[:200]}") from e
        trace = data.get("x_0g_trace") or {}
        return {"answer": answer.strip(), "model": data.get("model") or self.model, "trust_mode": self.trust or None,
                "provider": trace.get("provider"), "tee_verified": trace.get("tee_verified"),
                "ms": int((time.monotonic() - t0) * 1000), "usage": data.get("usage")}


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env reader: KEY=VALUE lines; real environment variables win."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass

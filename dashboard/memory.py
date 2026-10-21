"""A read-only memory store that serves the robot brain's memory API from a JSON file.

The brain keeps what it learns in three places: facts (short sentences, some with an expiry), documents (split
into chunks) and episodes (past conversations with a summary and their turns). This module serves the same JSON
shapes from a file, so the admin page's Memory tab works with no brain, and the same page can later point at a
real one.

Routes (same paths and shapes as the brain):
  GET  /memory/facts[?all=1]   {"facts": [{id, text, kind, entity, created, expires, source}]}
  GET  /memory/docs            {"documents": [{id, title, source, url, created, chars, chunks, unembedded}]}
  GET  /memory/episodes[?limit] {"episodes": [{id, started, ended, status, summary, open_loops, new_facts, model, turns}]}
  GET  /memory/episodes/{id}   {id, started, ..., "turns": [{ts, role, text}]}
  GET  /memory/brief           {"brief": "...", "pending_embeddings": 0}
  POST /memory/recall          {"query": "...", "k": 8} -> {"results": [{source, id, text, score, ...}]}

Recall here is keyword only (the brain mixes keywords with embeddings); the result shape is the same.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from aiohttp import web

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo_memory.json"

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "for", "is", "are", "was", "were", "be", "it",
         "my", "me", "i", "you", "your", "what", "when", "where", "who", "how", "do", "does", "did", "with", "about"}


def _words(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in _STOP:
            continue
        # a light plural and tense fold so "plants" finds "plant" and "watered" finds "water"
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 2 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def chunk_text(text: str, size: int = 600) -> list[str]:
    """Split on blank lines, then pack paragraphs into chunks of about `size` characters."""
    chunks: list[str] = []
    cur = ""
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if cur and len(cur) + len(para) + 2 > size:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return chunks


class MemoryStore:
    def __init__(self, path: Path = DEMO, now: Callable[[], float] | None = None, zg=None) -> None:
        self.zg = zg  # dashboard.zg.ZeroG, or None: "Ask Floaty" answers from memory through 0G Private Computer
        self.path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc).timestamp())
        raw: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        self.label: str = raw.get("label", "demo data")
        self.about: str = raw.get("about", "")
        self.profile: str = raw.get("profile", "")
        self._facts: list[dict] = sorted(raw.get("facts", []), key=lambda f: -_ts(f.get("created")))
        self._docs: list[dict] = []
        self._chunks: list[dict] = []
        for d in raw.get("documents", []):
            pieces = chunk_text(d.get("text", ""))
            self._docs.append({"id": d["id"], "title": d["title"], "source": d.get("source"), "url": d.get("url"),
                               "created": d.get("created"), "chars": len(d.get("text", "")), "chunks": len(pieces),
                               "unembedded": 0})
            for i, p in enumerate(pieces):
                self._chunks.append({"id": len(self._chunks) + 1, "doc_id": d["id"], "idx": i, "text": p,
                                     "title": d["title"], "url": d.get("url")})
        self._docs.sort(key=lambda d: -_ts(d.get("created")))
        self._episodes: list[dict] = sorted(raw.get("episodes", []), key=lambda e: -_ts(e.get("started")))

    # ------------------------------------------------------------------ reads
    def _live(self, f: dict) -> bool:
        return not f.get("expires") or _ts(f["expires"]) > self._now()

    def facts(self, include_expired: bool = False) -> list[dict]:
        keys = ("id", "text", "kind", "entity", "created", "expires", "source")
        return [{k: f.get(k) for k in keys} for f in self._facts if include_expired or self._live(f)]

    def documents(self) -> list[dict]:
        return [dict(d) for d in self._docs]

    @staticmethod
    def _episode_out(e: dict) -> dict:
        return {"id": e["id"], "started": e.get("started"), "ended": e.get("ended"), "status": e.get("status", "summarized"),
                "summary": e.get("summary"), "open_loops": list(e.get("open_loops") or []),
                "new_facts": list(e.get("new_facts") or []), "model": e.get("model")}

    def episodes(self, limit: int = 50) -> list[dict]:
        return [dict(self._episode_out(e), turns=len(e.get("turns") or [])) for e in self._episodes[:max(0, limit)]]

    def episode(self, episode_id: str) -> dict | None:
        for e in self._episodes:
            if e["id"] == episode_id:
                return dict(self._episode_out(e), turns=[{"ts": t.get("ts"), "role": t["role"], "text": t["text"]}
                                                         for t in e.get("turns") or []])
        return None

    def open_loops(self) -> list[str]:
        loops: list[str] = []
        for e in self._episodes:
            for x in e.get("open_loops") or []:
                if x not in loops:
                    loops.append(x)
        return loops

    def brief(self, max_facts: int = 25) -> str:
        """The memory block the brain would put in the voice prompt, built the same way."""
        parts = []
        if self.profile:
            parts.append("## About the user\n" + self.profile)
        facts = self.facts()
        if facts:
            rank = {"person": 0, "preference": 0, "fact": 1, "plan": 1, "event": 1, "reminder": 2}
            picked = sorted(facts, key=lambda f: (rank.get(f["kind"], 1), -_ts(f["created"])))[:max_facts]
            picked.sort(key=lambda f: -_ts(f["created"]))
            lines = [f"- {f['text']} (said {str(f['created'])[:10]}" + (f", until {f['expires'][:10]}" if f["expires"] else "") + ")"
                     for f in picked]
            parts.append("## Things to remember (newest first; when two disagree, the newer one is true)\n" + "\n".join(lines))
        loops = self.open_loops()[:8]
        if loops:
            parts.append("## Open loops from recent conversations\n" + "\n".join(f"- {x}" for x in loops))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ recall
    def recall(self, query: str, k: int = 5) -> list[dict]:
        """Keyword search over live facts, episode summaries and document chunks. Score is the share of query
        words found, so 1.0 means every word matched."""
        q = set(_words(query if isinstance(query, str) else ""))
        if not q:
            return []
        k = max(1, min(int(k or 5), 25))
        scored: list[tuple[float, float, dict]] = []

        def score(*texts: str) -> float:
            words = set()
            for t in texts:
                words.update(_words(t or ""))
            return len(q & words) / len(q)

        for f in self._facts:
            s = score(f["text"], f.get("entity") or "")
            if s and self._live(f):
                scored.append((s, _ts(f.get("created")), {"source": "fact", "id": f["id"], "text": f["text"],
                                                          "when": f.get("created"), "expires": f.get("expires")}))
        for e in self._episodes:
            s = score(e.get("summary") or "")
            if s and e.get("summary"):
                scored.append((s, _ts(e.get("started")), {"source": "episode", "id": e["id"], "text": e["summary"],
                                                           "when": e.get("started"), "open_loops": list(e.get("open_loops") or [])}))
        for c in self._chunks:
            s = score(c["text"], c["title"])
            if s:
                scored.append((s, 0.0, {"source": "doc", "id": c["id"], "text": c["text"], "doc_id": c["doc_id"],
                                        "title": c["title"], "url": c["url"], "chunk": c["idx"]}))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [dict(item, score=round(s, 3)) for s, _, item in scored[:k]]

    # ------------------------------------------------------------------ routes
    def routes(self, app: web.Application) -> None:
        async def meta(_: web.Request) -> web.Response:
            return web.json_response({"label": self.label, "about": self.about, "read_only": True,
                                      "ask": self.zg.info() if self.zg is not None else {"configured": False}})

        async def facts(req: web.Request) -> web.Response:
            return web.json_response({"facts": self.facts(include_expired=req.query.get("all") in ("1", "true"))})

        async def docs(_: web.Request) -> web.Response:
            return web.json_response({"documents": self.documents()})

        async def episodes(req: web.Request) -> web.Response:
            try:
                limit = int(req.query.get("limit", "50"))
            except ValueError:
                limit = 50
            return web.json_response({"episodes": self.episodes(limit)})

        async def episode(req: web.Request) -> web.Response:
            ep = self.episode(req.match_info["id"])
            return web.json_response(ep if ep else {"error": "not found"}, status=200 if ep else 404)

        async def brief(_: web.Request) -> web.Response:
            return web.json_response({"brief": self.brief(), "pending_embeddings": 0})

        async def recall(req: web.Request) -> web.Response:
            try:
                b = await req.json()
            except (json.JSONDecodeError, ValueError):
                b = {}
            if not isinstance(b, dict):
                b = {}
            q = b.get("query", "")
            if not isinstance(q, str):
                return web.json_response({"ok": False, "error": "query must be a string"}, status=400)
            try:
                k = int(b.get("k") or 5)
            except (TypeError, ValueError):
                return web.json_response({"ok": False, "error": "k must be a number"}, status=400)
            return web.json_response({"results": self.recall(q, k)})

        async def ask(req: web.Request) -> web.Response:
            """Answer a question from memory: keyword recall picks the context, a 0G model writes the answer."""
            try:
                b = await req.json()
            except (json.JSONDecodeError, ValueError):
                b = {}
            q = (b.get("question") if isinstance(b, dict) else "") or ""
            if not isinstance(q, str) or not q.strip():
                return web.json_response({"ok": False, "error": "question required"}, status=400)
            if self.zg is None or not self.zg.configured:
                return web.json_response({"ok": False, "setup": True, "error": "Set ZG_API_KEY (see .env.example) to "
                                          "ask Floaty through 0G Private Computer. Get a key at pc.0g.ai."})
            hits = self.recall(q, 8)
            context = "\n".join(f"- ({h['source']}) {h['text']}" for h in hits) or "- (nothing matched)"
            messages = [
                {"role": "system", "content": "You are Floaty, a small desk robot. Answer the user's question in one "
                 "to three short sentences, using only the memory below. If the memory does not say, say you don't "
                 "know yet.\n\nAbout the user:\n" + self.brief() + "\n\nMemory that matched the question:\n" + context},
                {"role": "user", "content": q.strip()},
            ]
            from .zg import ZeroGError  # noqa: PLC0415

            try:
                r = await self.zg.chat(messages)
            except ZeroGError as e:
                return web.json_response({"ok": False, "error": str(e), "status": e.status})
            except Exception as e:  # noqa: BLE001
                return web.json_response({"ok": False, "error": f"0G call failed: {e.__class__.__name__}"})
            return web.json_response({"ok": True, **r, "used": hits})

        app.router.add_post("/memory/ask", ask)
        app.router.add_get("/memory/meta", meta)
        app.router.add_get("/memory/facts", facts)
        app.router.add_get("/memory/docs", docs)
        app.router.add_get("/memory/episodes", episodes)
        app.router.add_get("/memory/episodes/{id}", episode)
        app.router.add_get("/memory/brief", brief)
        app.router.add_post("/memory/recall", recall)

"""Live sensor and hearing dashboard for a desk bot.

The robot opens a websocket to /robot/telemetry (headers X-Device, X-Firmware) while someone has the dashboard
open, and for LINGER_S after the last viewer leaves. It sends:
- text {"t":"sensors", ...} at sensors_hz (mic level, imu, proximity, head and screen touch, power, servos, ...)
- text {"t":"event","name":...} for taps, shakes, wake words, silent mode, sessions
- binary frames of raw PCM16 16 kHz mono mic audio while the hub asks for audio ({"t":"config","audio":true})

A host app can also publish conversation audio (session_audio) and transcript lines (note). With an optional
speech-to-text object (any object with `async transcribe(pcm) -> {"text": ...}`), audio is transcribed live:
a partial every second, a final at the end of each utterance.

Nothing is stored: audio and transcripts go only to open pages.

Viewer websocket /sensors/ws: binary frames are 1 byte source (0 robot, 1 session) + PCM16 16 kHz mono; text
frames are the JSON above plus {"t":"heard"} lines and {"t":"status"}. The viewer may send {"t":"audio","on":bool}
and {"t":"hz","value":n}. See PROTOCOL.md.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import time
from array import array
from pathlib import Path
from typing import Callable

from aiohttp import WSMsgType, web

log = logging.getLogger("dashboard")

LINGER_S = 30.0  # keep the robot streaming this long after the last viewer leaves
SESSION_AUDIO_S = 0.6  # robot frames are dropped while session audio arrived this recently (no double audio)
VIEWER_QUEUE = 400  # frames per viewer before the oldest is dropped (about 10 s of audio)
FRAME = 320  # 20 ms VAD frames at 16 kHz
PARTIAL_S = 1.0
END_QUIET_S = 0.7
MAX_UTTER_S = 12.0
MIN_SPEECH_S = 0.3
PAGE = Path(__file__).with_name("sensors.html")


def _db(samples) -> float:
    if not samples:
        return -120.0
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    return 20 * math.log10(max(rms, 1.0) / 32768)


class Utterances:
    """Energy VAD with an adaptive noise floor over a PCM16 stream; partial transcripts while speech goes on and a
    final transcript at its end. One transcription at a time; a partial that would queue behind another is skipped."""

    def __init__(self, stt, emit: Callable[[dict], None]) -> None:
        self.stt = stt
        self.emit = emit
        self.floor = -60.0
        self.buf = bytearray()
        self.rest = b""
        self.speech = False
        self.voiced = 0  # 20 ms frames above the threshold in this utterance
        self.quiet = 0
        self.since_partial = 0
        self.pre = collections.deque(maxlen=15)  # 0.3 s pre-roll
        self.src = "robot"
        self.n = 0
        self.busy = False
        self._tasks: set[asyncio.Task] = set()

    def feed(self, pcm: bytes, src: str) -> None:
        if src != self.src and self.speech:
            self._end()
        self.src = src
        data = self.rest + pcm
        whole = len(data) // (FRAME * 2) * FRAME * 2
        self.rest = data[whole:]
        for i in range(0, whole, FRAME * 2):
            self._frame(data[i:i + FRAME * 2])

    def _frame(self, frame: bytes) -> None:
        db = _db(array("h", frame))
        loud = db > self.floor + 9 and db > -62
        if not self.speech:
            # The floor follows the quiet quickly and the loud slowly
            self.floor += (db - self.floor) * (0.05 if db < self.floor else 0.005)
            self.pre.append(frame)
            if loud:
                self.speech, self.voiced, self.quiet, self.since_partial = True, 1, 0, 0
                self.n += 1
                self.buf = bytearray(b"".join(self.pre))
            return
        self.buf += frame
        self.since_partial += 1
        if loud:
            self.voiced += 1
            self.quiet = 0
        else:
            self.quiet += 1
        secs = len(self.buf) / 32000
        if self.quiet * 0.02 >= END_QUIET_S or secs >= MAX_UTTER_S:
            self._end()
        elif self.since_partial * 0.02 >= PARTIAL_S and self.voiced * 0.02 >= MIN_SPEECH_S and not self.busy:
            self.since_partial = 0
            self._run(bytes(self.buf), final=False)

    def _end(self) -> None:
        if self.voiced * 0.02 >= MIN_SPEECH_S:
            self._run(bytes(self.buf), final=True)
        else:
            self.emit({"t": "heard", "id": self.n, "final": True, "drop": True, "source": "stt", "src": self.src})
        self.speech, self.buf, self.voiced, self.quiet = False, bytearray(), 0, 0
        self.pre.clear()

    def _run(self, pcm: bytes, final: bool) -> None:
        uid, src = self.n, self.src

        async def go() -> None:
            self.busy = True
            try:
                r = await self.stt.transcribe(pcm)
                self.emit({"t": "heard", "id": uid, "final": final, "source": "stt", "src": src,
                           "text": r.get("text", ""), "ms": r.get("ms"), "dur_ms": len(pcm) // 32,
                           "db": round(_db(array("h", pcm)), 1)})
            except Exception as e:  # noqa: BLE001
                self.emit({"t": "heard", "id": uid, "final": final, "source": "stt", "error": repr(e)[:120]})
            finally:
                self.busy = False

        task = asyncio.get_running_loop().create_task(go())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


class SensorHub:
    def __init__(self, stt=None, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.viewers: dict[web.WebSocketResponse, dict] = {}
        self.robot: web.WebSocketResponse | None = None
        self.robot_info: dict = {}
        self.latest: dict = {}
        self.latest_at = 0.0
        self.recent = collections.deque(maxlen=150)  # events and heard lines for a viewer that joins late
        self.hz = 10
        self.audio = True
        self._last_viewer_left = 0.0
        self._session_audio_at = 0.0
        self.utter = Utterances(stt, self._publish) if stt is not None else None
        self.counts = {"frames": 0, "events": 0, "audio_bytes": 0, "robot_connects": 0, "viewer_drops": 0}

    # ------------------------------------------------------------------ state
    def wanted(self) -> bool:
        return bool(self.viewers) or self.clock() - self._last_viewer_left < LINGER_S

    def link_fields(self) -> dict:
        return {"telemetry": self.wanted()}

    def status(self) -> dict:
        return {"viewers": len(self.viewers), "wanted": self.wanted(), "robot": self.robot is not None,
                "robot_info": self.robot_info, "hz": self.hz, "audio": self.audio, "counts": self.counts,
                "latest_age_s": round(self.clock() - self.latest_at, 1) if self.latest_at else None}

    # -------------------------------------------------------------- publishing
    def _publish(self, obj: dict, keep: bool = True) -> None:
        obj.setdefault("at", round(self.clock(), 3))
        if keep:
            self.recent.append(obj)
        text = json.dumps(obj, separators=(",", ":"))
        for q in self.viewers.values():
            self._put(q, text)

    def _put(self, viewer: dict, item) -> None:
        q: asyncio.Queue = viewer["q"]
        if isinstance(item, bytes) and not viewer.get("audio", True):
            return
        if q.full():
            try:
                q.get_nowait()
                self.counts["viewer_drops"] += 1
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(item)

    def _audio(self, pcm: bytes, src: str) -> None:
        if not pcm:
            return
        self.counts["audio_bytes"] += len(pcm)
        if not self.viewers:
            return
        frame = (b"\x01" if src == "session" else b"\x00") + pcm
        for v in self.viewers.values():
            self._put(v, frame)
        if self.utter is not None:
            self.utter.feed(pcm, src)

    # Hooks from the rest of the brain (cheap no-ops while nobody watches)
    def session_audio(self, pcm: bytes) -> None:
        """Audio a host app sends to its voice model during a conversation (16 kHz PCM16)."""
        self._session_audio_at = time.monotonic()
        self._audio(pcm, "session")

    def note(self, kind: str, text: str, **extra) -> None:
        """A line of what was heard or said, e.g. realtime (user transcript), assistant, listen (idle segment)."""
        if self.viewers:
            self._publish({"t": "heard", "source": kind, "final": True, "text": text, **extra})

    # ------------------------------------------------------------------ robot
    async def _send_config(self) -> None:
        if self.robot is not None and not self.robot.closed:
            try:
                await self.robot.send_str(json.dumps({"t": "config", "sensors_hz": self.hz, "audio": self.audio}))
            except Exception:  # noqa: BLE001
                pass

    async def robot_ws(self, req: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1024 * 1024)
        await ws.prepare(req)
        old, self.robot = self.robot, ws
        if old is not None and not old.closed:
            await old.close()
        self.counts["robot_connects"] += 1
        self.robot_info = {"device": req.headers.get("X-Device", ""), "firmware": req.headers.get("X-Firmware", ""),
                           "since": round(self.clock(), 1), "addr": req.remote}
        log.info("telemetry: robot connected (%s, fw %s)", self.robot_info["device"], self.robot_info["firmware"])
        self._publish({"t": "robot", "connected": True, **self.robot_info}, keep=False)
        await self._send_config()
        closer = asyncio.create_task(self._close_when_unwatched(ws))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    if time.monotonic() - self._session_audio_at > SESSION_AUDIO_S:
                        self._audio(bytes(msg.data[: len(msg.data) // 2 * 2]), "robot")
                elif msg.type == WSMsgType.TEXT:
                    try:
                        obj = json.loads(msg.data)
                    except ValueError:
                        continue
                    t = obj.get("t")
                    if t == "sensors":
                        self.counts["frames"] += 1
                        self.latest, self.latest_at = obj, self.clock()
                        self._publish(obj, keep=False)
                    elif t == "event":
                        self.counts["events"] += 1
                        self._publish(obj)
                    else:
                        self._publish(obj, keep=False)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            closer.cancel()
            if self.robot is ws:
                self.robot = None
                self._publish({"t": "robot", "connected": False}, keep=False)
            log.info("telemetry: robot disconnected")
        return ws

    async def _close_when_unwatched(self, ws: web.WebSocketResponse) -> None:
        while not ws.closed:
            await asyncio.sleep(5)
            if not self.wanted():
                log.info("telemetry: no viewers for %.0f s, closing the robot stream", LINGER_S)
                await ws.close()

    # ----------------------------------------------------------------- viewers
    async def viewer_ws(self, req: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(req)
        viewer = {"q": asyncio.Queue(maxsize=VIEWER_QUEUE), "audio": True}
        self.viewers[ws] = viewer
        sender = asyncio.create_task(self._drain(ws, viewer["q"]))
        await ws.send_str(json.dumps({"t": "hello", "status": self.status(), "latest": self.latest,
                                      "recent": list(self.recent)[-60:]}))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    obj = json.loads(msg.data)
                except ValueError:
                    continue
                if obj.get("t") == "audio":
                    viewer["audio"] = bool(obj.get("on"))
                    want = any(v["audio"] for v in self.viewers.values())
                    if want != self.audio:
                        self.audio = want
                        await self._send_config()
                elif obj.get("t") == "hz":
                    self.hz = max(1, min(20, int(obj.get("value") or 10)))
                    await self._send_config()
        finally:
            sender.cancel()
            self.viewers.pop(ws, None)
            if not self.viewers:
                self._last_viewer_left = self.clock()
                self.audio = True  # the next viewer starts with audio
                # What was heard stays only while someone watches; robot events may stay
                self.recent = collections.deque((o for o in self.recent if o.get("t") != "heard"),
                                                maxlen=self.recent.maxlen)
        return ws

    @staticmethod
    async def _drain(ws: web.WebSocketResponse, q: asyncio.Queue) -> None:
        try:
            while not ws.closed:
                item = await q.get()
                if isinstance(item, bytes):
                    await ws.send_bytes(item)
                else:
                    await ws.send_str(item)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            log.debug("viewer send failed: %r", e)

    # ------------------------------------------------------------------ routes
    def routes(self, app: web.Application) -> None:
        async def page(_: web.Request) -> web.Response:
            return web.Response(text=PAGE.read_text(), content_type="text/html")

        async def status(_: web.Request) -> web.Response:
            return web.json_response({**self.status(), "latest": self.latest})

        app.router.add_get("/sensors", page)
        app.router.add_get("/sensors/", page)
        app.router.add_get("/sensors/status", status)
        app.router.add_get("/sensors/ws", self.viewer_ws)
        app.router.add_get("/robot/telemetry", self.robot_ws)

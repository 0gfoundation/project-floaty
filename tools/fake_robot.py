"""A fake robot for trying the dashboard without hardware.

python tools/fake_robot.py [--url ws://localhost:8175/robot/telemetry]

Streams sensor frames at the rate the hub asks for, a few events, and 16 kHz PCM16 audio: room noise with a
short burst of tone every few seconds so the meter, waveform and spectrogram have something to show.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import struct
import time

import aiohttp

RATE = 16000
FRAME_S = 0.04  # 40 ms audio frames, like the firmware


def audio_frame(t0: float, n: int) -> bytes:
    out = []
    for i in range(n):
        t = t0 + i / RATE
        s = random.gauss(0, 120)  # room noise
        if (t % 5.0) < 1.2:  # "speech": a wobbling tone for 1.2 s every 5 s
            f = 220 + 80 * math.sin(2 * math.pi * 3 * t)
            s += 5000 * math.sin(2 * math.pi * f * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 4 * t))
        out.append(max(-32768, min(32767, int(s))))
    return struct.pack(f"<{n}h", *out)


def sensors(t: float, start: float) -> dict:
    speaking = (t % 5.0) < 1.2
    near = (t % 20.0) < 4.0
    return {
        "t": "sensors", "ms": int((t - start) * 1000), "state": "idle",
        "mic": {"src": "idle", "db": -22.0 if speaking else -55.0 + random.random() * 3, "peak_db": -12.0 if speaking else -48.0,
                "floor_db": -56.0, "vad": speaking, "seg": speaking, "mww": 0.02},
        "imu": {"ax": round(random.gauss(0, .01), 3), "ay": round(.97 + random.gauss(0, .01), 3), "az": round(.15 + random.gauss(0, .01), 3),
                "gx": round(random.gauss(0, .3), 1), "gy": round(random.gauss(0, .3), 1), "gz": round(random.gauss(0, .3), 1)},
        "prox": {"ps": 900 if near else random.randint(0, 20), "als": round(120 + 10 * math.sin(t / 30), 1)},
        "head": {"raw": [0, 0, 0], "pressed": False, "pos": 0},
        "screen": {"pressed": False, "x": -1, "y": -1},
        "power": {"bat_v": 4.15, "bat_pct": 98, "charging": False, "usb": True, "vbus_v": 4.94, "bat_present": True, "sys_v": 4.37},
        "servo": {"yaw": round(10 * math.sin(t / 7), 1), "pitch": 5.0, "yaw_target": round(10 * math.sin(t / 7), 1), "pitch_target": 5.0},
        "sys": {"heap": 47000, "heap_min": 27000, "psram": 6378968, "uptime_s": int(t - start), "fw": "fake-1.0", "drops": 0, "chip_c": 45.0},
        "speaker": {"volume": 70, "playing": False}, "cam": {"present": True}, "silent": False, "sleep": False,
    }


async def run(url: str) -> None:
    cfg = {"sensors_hz": 10, "audio": True}
    start = time.time()
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url, headers={"X-Device": "fa:ke:00:00:00:01", "X-Firmware": "fake-1.0"}) as ws:
            print("connected; Ctrl-C to stop")

            async def read() -> None:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        obj = json.loads(msg.data)
                        if obj.get("t") == "config":
                            cfg.update({k: obj[k] for k in ("sensors_hz", "audio") if k in obj})

            reader = asyncio.create_task(read())
            next_sensor, next_event, t_audio = time.time(), time.time() + 3, time.time()
            n = int(RATE * FRAME_S)
            while not ws.closed:
                now = time.time()
                if cfg["audio"]:
                    await ws.send_bytes(audio_frame(t_audio - start, n))
                t_audio += FRAME_S
                if now >= next_sensor:
                    await ws.send_str(json.dumps(sensors(now, start)))
                    next_sensor = now + 1 / max(1, cfg["sensors_hz"])
                if now >= next_event:
                    await ws.send_str(json.dumps({"t": "event", "name": random.choice(["tap", "swipe_forward", "press", "release", "screen_tap"])}))
                    next_event = now + random.uniform(4, 9)
                await asyncio.sleep(max(0.0, t_audio - time.time()))
            reader.cancel()
    print("hub closed the stream (nobody is watching)")


async def forever(url: str) -> None:
    # Like the firmware: reconnect every 5 s, so opening the page brings the stream back
    while True:
        try:
            await run(url)
        except (aiohttp.ClientError, OSError) as e:
            print(f"cannot reach the hub ({e.__class__.__name__}), retrying")
        await asyncio.sleep(5)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="ws://localhost:8175/robot/telemetry")
    try:
        asyncio.run(forever(p.parse_args().url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

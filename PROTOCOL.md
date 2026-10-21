# Telemetry protocol

Three parties: the **robot**, the **hub** (`dashboard/hub.py`), and any number of **viewers** (the page).

## When the robot streams

The robot polls its host app and gets `"telemetry": true` while someone watches (and for 30 s after the last
viewer leaves). The standalone server answers that at `GET /link` as `{"telemetry": true|false}`. When it is
true, the robot opens a websocket to `ws://<hub>/robot/telemetry` with headers `X-Device` (MAC) and
`X-Firmware` (version). The hub closes it when nobody has watched for 30 s; the robot retries at most every 5 s.

## Hub to robot

```json
{"t": "config", "sensors_hz": 10, "audio": true}
```

`sensors_hz` is 1 to 20. `audio` asks for mic audio; it turns off when no viewer wants sound.

## Robot to hub

Text, at `sensors_hz`:

```json
{"t": "sensors", "ms": 35900381, "state": "idle",
 "mic":   {"src": "idle", "db": -52.1, "peak_db": -44.0, "floor_db": -56.0, "vad": false, "seg": false, "mww": 0.02},
 "imu":   {"ax": 0.0, "ay": 0.97, "az": 0.15, "gx": 0.4, "gy": -0.1, "gz": 0.1},
 "prox":  {"ps": 12, "als": 6.4},
 "head":  {"raw": [0, 0, 0], "pressed": false, "pos": 0},
 "screen":{"pressed": false, "x": -1, "y": -1},
 "power": {"bat_v": 4.15, "bat_pct": 100, "charging": false, "usb": true, "vbus_v": 4.94},
 "servo": {"yaw": 1.5, "pitch": 8.7, "yaw_target": 1.5, "pitch_target": 8.7},
 "sys":   {"heap": 47135, "heap_min": 27023, "uptime_s": 35900, "fw": "2.6.0", "drops": 0, "chip_c": 46.7},
 "speaker": {"volume": 100, "playing": false}, "silent": false, "sleep": false}
```

| Field | Source on the robot | Unit |
|---|---|---|
| `mic.db`, `peak_db`, `floor_db` | mic channel level, peak and adaptive noise floor | dBFS |
| `mic.src` | `idle` (always-on listener), `session` (conversation), `off` (muted) | |
| `imu` | BMI270 accelerometer and gyroscope | g, deg/s |
| `prox.ps`, `prox.als` | LTR-553 proximity (raw counts) and ambient light | counts, lux |
| `head` | Si12T head touch, 3 channels | 0 to 3 |
| `screen` | FT6336 touch | px |
| `power` | AXP2101 | V, % |
| `servo` | commanded angles, not measured | deg |

Text, on each discrete event: `{"t": "event", "name": "tap"}`. Names in use: `tap`, `double_tap`, `press`,
`release`, `swipe_forward`, `swipe_backward`, `shake`, `screen_tap`, `wake_word`, `silent_on`, `silent_off`,
`session_start`, `session_end`, `segment_sent`.

Binary, while `audio` is on: PCM16 little-endian, 16 kHz, mono, 40 to 64 ms per frame.

## Hub to viewer (`/sensors/ws`)

- First a `{"t": "hello", "status": ..., "latest": ..., "recent": [...]}`.
- Then every text message above, plus `{"t": "robot", "connected": bool}` and `{"t": "heard", ...}` transcript
  lines when a speech-to-text backend is plugged in.
- Binary frames are 1 byte source (`0` robot mic, `1` conversation audio) followed by the PCM.

Viewers may send `{"t": "audio", "on": false}` (no sound for this viewer) and `{"t": "hz", "value": 5}`.

## Plugging in speech-to-text

`SensorHub(stt=...)` takes any object with `async transcribe(pcm: bytes) -> {"text": str}`. The hub then
transcribes what the robot hears: a partial every second while someone speaks, a final after 0.7 s of quiet.

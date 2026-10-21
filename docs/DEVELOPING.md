# Developing the Floaty dashboard

## Run options

```bash
uv run python -m dashboard.server --tabs dashboard,sensors   # pick tabs by id or label
uv run python -m dashboard.server --tabs all                 # every tab that has a backend
uv run python -m dashboard.server --memory path/to/file.json # your own memory file
```

`/#memory` opens a tab directly. The Sensors page also works on its own at `/sensors`.

## How the tabs work

The tab bar comes from one list, `TABS` in [`dashboard/config.py`](../dashboard/config.py), and the ids switched
on by default, `ENABLED`. The page reads it from `GET /api/tabs`. A tab is live when it is switched on and the
server has its backend; otherwise it shows as a disabled "later" chip. Every tab's markup and script already sit in
`dashboard/admin.html`.

- **Dashboard** serves the same shapes as the brain's overview (`GET /admin/overview`, `GET /usage`) from
  `data/demo_dashboard.json` ([`dashboard/admin.py`](../dashboard/admin.py)). Times are stored as "seconds ago",
  so the demo always looks current. Approve, Cancel and Toggle follow only change demo state. While a robot streams
  to the hub, the Robot card shows its live link.
- **Memory** serves the brain's memory routes (`/memory/facts`, `/memory/docs`, `/memory/episodes`,
  `/memory/episodes/{id}`, `/memory/brief`, `POST /memory/recall`, `POST /memory/ask`) from a JSON file
  ([`dashboard/memory.py`](../dashboard/memory.py)). Search here is keyword only.
- **Sensors** is the live sensor page, in a frame that exists only while the tab is open, so leaving the tab lets
  the robot stop streaming.

## Adding a tab

1. Write its backend: a module with a `routes(app)` that serves the routes its loader in `admin.html` calls, from
   demo data in `data/` (labelled DEMO DATA in the page) or a real source.
2. In `make_app` in [`dashboard/server.py`](../dashboard/server.py), call those routes when the tab is enabled and
   add its id to `available`.
3. Add the id to `ENABLED` (or start with `--tabs`), add a test in `tests/`, and a screenshot in the README.

## Ask Floaty settings

All from the environment or a git-ignored `.env` (see `.env.example`):

| Setting | Default | Meaning |
| --- | --- | --- |
| `ZG_API_KEY` | none | Your 0G PC key; without it Ask stays off |
| `ZG_BASE_URL` | `https://router-api.0g.ai/v1` | The 0G PC endpoint |
| `ZG_MODEL` | `0gm-1.0-35b-a3b` | The model; any 0G PC model that runs in an enclave works in private mode |
| `ZG_TRUST_MODE` | `private` | `private` runs in a TDX enclave and fails closed; empty sends no trust header |

The client is one chat call in [`dashboard/zg.py`](../dashboard/zg.py), with the header
`X-0G-Provider-Trust-Mode`.

## Connecting a real robot

The robot opens a websocket to `/robot/telemetry` while someone has the Sensors tab open; `GET /link` tells it
whether anyone is watching. Everything it sends is in [PROTOCOL.md](../PROTOCOL.md): sensor frames at 1 to 20 Hz,
events, and raw 16 kHz mic audio. The firmware side (a small telemetry module for the robot's ESP32-S3 board) will
be open sourced with the firmware.

## Design notes

- **Streams only while watched.** The robot streams while the Sensors tab is open and for 30 s after; nothing is
  stored.
- **Audio never blocks the robot.** On the robot, audio hooks only copy into a ring buffer; one low-priority task
  does the network work and counts drops.
- **Live transcripts are pluggable.** Pass any `async transcribe(pcm) -> {"text": ...}` object as
  `SensorHub(stt=...)` for a partial transcript every second and a final one after 0.7 s of quiet.
- **Demo first, same shapes.** Memory and the Dashboard serve made-up data in the brain's shapes, so the page can
  later point at a real brain unchanged.

## Tests

```bash
uv run --extra test python -m pytest
```

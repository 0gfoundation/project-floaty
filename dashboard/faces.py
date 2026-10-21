"""The robot's face expressions and head gestures, and the page that previews them without a robot.

A move is [yaw, pitch, speed, hold ms]: yaw left -40 .. 40 right, pitch an offset from level (+ up), speed in servo
units (60 slow .. 1000 snappy), hold how long the step lasts before the next one starts. On the robot the moves play
relative to where the user is; the preview plays them as is.

Mood words are the emotion names a language model can ask for; each maps to the expression whose face and moves
fit it best.
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

Move = list[int]

PAGE = Path(__file__).with_name("faces.html")
IMAGES = Path(__file__).with_name("static") / "faces"

# name -> (what the face shows, head moves)
EXPRESSIONS: dict[str, tuple[str, list[Move]]] = {
    "idle": ("Waiting for you: blinks, glances, gentle breathing; small open mouth.",
             [[-15, 2, 200, 1200], [0, 0, 200, 1000], [15, 2, 200, 1200], [0, 0, 200, 500]]),
    "listening": ("Mic open: wide eyes, big pupils, dot mouth, cyan pulse; head tilts toward you.", [[8, 6, 250, 800]]),
    "thinking": ("Looks up and away, right eye squints under a raised brow, cyan question mark.", [[18, 14, 150, 800]]),
    "speaking": ("Mouth chatters like speech, soft smiling eyes, small nods.",
                 [[0, 3, 300, 400], [0, -1, 300, 400], [0, 3, 300, 400], [0, 0, 300, 400]]),
    "happy": ("Cheeks lift the eyes, yellow sparks, big smile, a nod.",
              [[0, 9, 500, 350], [0, -3, 500, 350], [0, 9, 500, 350], [0, 0, 400, 400]]),
    "excited": ("Star pupils, sparks and twinkles, bouncing.",
                [[0, 12, 800, 250], [0, 0, 800, 250], [0, 12, 800, 250], [0, 0, 800, 250], [0, 6, 500, 300]]),
    "laughing": ("> < eyes, wide smile, bouncing, playful head wobble.",
                 [[-6, 8, 700, 250], [6, 2, 700, 250], [-6, 8, 700, 250], [6, 2, 700, 250], [0, 4, 500, 300]]),
    "confused": ("One brow up, one down, pupils apart, question mark and sweat drop; head tilted.", [[-16, 6, 200, 1000]]),
    "sad": ("Drooping outer lids, worried brows, eyes down, a tear; head lowers slowly.", [[0, -10, 120, 1000]]),
    "surprised": ("Big round eyes, small pupils, raised brows, round open mouth; head pops up.", [[0, 14, 900, 600]]),
    "sleepy": ("Half-closed lids, slow heavy blinks; head sinks.", [[0, -6, 80, 1500]]),
    "sleeping": ("Closed crescents, slow breathing, floating Zz; head rests.", [[0, -12, 60, 2000]]),
    "angry": ("Angled brows, narrowed lids, red anger mark; quick head shake.",
              [[-8, 0, 700, 200], [8, 0, 700, 200], [-8, 0, 700, 200], [8, 0, 700, 200], [0, -2, 500, 300]]),
    "love": ("Heart pupils, floating hearts, blush; gentle sway.",
             [[-10, 6, 150, 900], [10, 6, 150, 900], [-10, 6, 150, 900], [0, 6, 150, 600]]),
    "error": ("Concerned face, worried brows, amber warning and sweat; small shake.",
              [[-5, -2, 500, 250], [5, -2, 500, 250], [-5, -2, 500, 250], [0, -2, 400, 300]]),
    "silent": ("Muted: calm half-closed eyes, closed mouth, mute badge; head lowers a little.", [[0, -4, 120, 1000]]),
}

# Mood word -> expression. "idle" as a mood means "no special head move" on the robot.
MOODS: dict[str, str] = {
    "neutral": "idle", "relaxed": "idle",
    "happy": "happy", "winking": "happy", "cool": "happy", "delicious": "happy", "confident": "happy",
    "laughing": "laughing", "funny": "laughing", "silly": "laughing",
    "excited": "excited",
    "sad": "sad", "crying": "sad",
    "angry": "angry",
    "loving": "love", "kissy": "love", "embarrassed": "love",
    "surprised": "surprised", "shocked": "surprised",
    "thinking": "thinking",
    "confused": "confused",
    "sleepy": "sleepy",
}

# Gestures a model can ask for on their own: name -> (what it looks like, moves, expression it borrows moves from)
GESTURES: dict[str, tuple[str, list[Move], str | None]] = {
    "nod": ("A yes nod: up, down, up, settle.",
            [[0, 8, 500, 300], [0, -2, 500, 300], [0, 6, 500, 300], [0, 0, 400, 300]], None),
    "shake": ("A no shake: left, right, smaller left, centre.",
              [[-12, 0, 600, 250], [12, 0, 600, 250], [-8, 0, 600, 250], [0, 0, 500, 300]], None),
    "tilt": ("A curious head tilt to one side.", [[-16, 6, 200, 1000]], None),
    "bounce": ("Quick happy bounces (the excited moves).", EXPRESSIONS["excited"][1], "excited"),
    "wiggle": ("A playful side to side wobble (the laughing moves).", EXPRESSIONS["laughing"][1], "laughing"),
    "look_around": ("A slow look left, then right, then back.", [[-25, 4, 200, 900], [25, 4, 200, 900], [0, 0, 200, 400]], None),
    "bow": ("A polite bow: head down, hold, back up.", [[0, -12, 250, 900], [0, 0, 200, 400]], None),
}


def has_image(name: str) -> bool:
    return (IMAGES / f"{name}.png").is_file()


def faces_payload() -> dict:
    """Everything the Faces tab shows, as JSON."""
    expressions = [
        {"name": n, "description": d, "moves": m, "moods": [w for w, e in MOODS.items() if e == n],
         "image": f"/static/faces/{n}.png" if has_image(n) else None,
         "duration_ms": sum(x[3] for x in m)}
        for n, (d, m) in EXPRESSIONS.items()
    ]
    gestures = [
        {"name": n, "description": d, "moves": m, "same_as": same, "duration_ms": sum(x[3] for x in m)}
        for n, (d, m, same) in GESTURES.items()
    ]
    return {"expressions": expressions, "gestures": gestures, "moods": MOODS,
            "move_format": ["yaw", "pitch", "speed", "hold_ms"]}


def routes(app: web.Application) -> None:
    async def page(_: web.Request) -> web.Response:
        return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html")

    async def data(_: web.Request) -> web.Response:
        return web.json_response(faces_payload())

    app.router.add_get("/faces", page)
    app.router.add_get("/api/faces", data)

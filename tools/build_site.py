"""Build the GitHub Pages site: Floaty's faces and head moves, simulated in the browser.

    uv run python tools/build_site.py      # writes site/ (git-ignored); open site/index.html or serve it

The page is the dashboard's Faces tab made static: faces.json instead of /api/faces, images next to it.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard import faces  # noqa: E402
OUT = ROOT / "site"
REPO = "https://github.com/0gfoundation/project-floaty"

HERO = f"""
  <div class="hero">
    <img src="floaty.png" alt="Floaty, a little robot with heart eyes, floating" width="220">
    <div>
      <h2>Floaty's faces</h2>
      <p>Floaty is a palm-sized AI agent with a face, built in public by the 0G DevRel team. Its brain thinks on
      <a href="https://pc.0g.ai">0G PC</a>. Pick a face below to play it with the real head moves the robot makes:
      yaw, pitch, speed and hold, exactly as the servos get them.</p>
      <p class="links"><a href="{REPO}">Code on GitHub</a> · <a href="{REPO}/blob/main/ROADMAP.md">Roadmap</a></p>
    </div>
  </div>
"""
STYLE = """
.hero { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 24px; margin: 6px 0 4px; }
.hero img { max-width: 100%; height: auto; }
.hero > div { flex: 1 1 320px; min-width: 0; }
.hero h2 { margin: 0 0 6px; font: 600 22px var(--sans); color: var(--strong); }
.hero p { margin: 0 0 8px; max-width: 64ch; color: var(--fg); }
.hero a { color: var(--violet); }
.links { font: 13px var(--mono); }
</style>"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "faces").mkdir(parents=True)
    data = faces.faces_payload()
    for e in data["expressions"]:
        if e.get("image"):
            name = Path(e["image"]).name
            shutil.copy(faces.IMAGES / name, OUT / "faces" / name)
            e["image"] = f"faces/{name}"
    (OUT / "faces.json").write_text(json.dumps(data, indent=1))
    page = (ROOT / "dashboard" / "faces.html").read_text()
    for old, new in [('fetch("/api/faces")', 'fetch("faces.json")'),
                     ("Could not load /api/faces.", "Could not load faces.json."),
                     ("<title>Robot Faces</title>", "<title>Floaty's Faces</title>"),
                     ("</style>", STYLE)]:
        assert old in page, old
        page = page.replace(old, new, 1)
    marker = "<header>"
    assert marker in page
    page = page.replace(marker, HERO + "  " + marker, 1)
    (OUT / "index.html").write_text(page)
    shutil.copy(ROOT / "docs" / "floaty.png", OUT / "floaty.png")
    (OUT / ".nojekyll").write_text("")
    print(f"site/ ready: {len(data['expressions'])} faces, {len(data['gestures'])} gestures")


if __name__ == "__main__":
    main()

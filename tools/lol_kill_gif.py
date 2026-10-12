"""Preview the same bounded match-flow GIF used by automatic LoL reports.

Usage: PYTHONPATH=. .venv/bin/python tools/lol_kill_gif.py
Local inputs and output under data/lol-gif-preview/ are ignored by Git.
"""
import json
from pathlib import Path
from PIL import Image
from imposter_finder.analysis.lol_flow import render

OUT = Path(__file__).resolve().parents[1] / "data/lol-gif-preview"


def main() -> None:
    match = json.loads((OUT / "match.json").read_text())
    timeline = json.loads((OUT / "timeline.json").read_text())
    names = json.loads((OUT / "names.json").read_text())
    data, preview = render(match, timeline, set(names))
    path = OUT / f"{match['metadata']['matchId']}-flow.gif"
    path.write_bytes(data)
    preview.save(OUT / "flow-preview.png")
    with Image.open(path) as gif:
        frames = gif.n_frames
        seconds = sum((gif.seek(n), gif.info.get("duration", 0))[1] for n in range(frames)) / 1000
    print(json.dumps({"path": str(path), "frames": frames, "seconds": round(seconds, 1),
                      "mb": round(len(data) / 1024 / 1024, 2)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

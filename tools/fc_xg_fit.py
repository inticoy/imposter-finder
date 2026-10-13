"""DB에 쌓인 FC 친구전 슈팅으로 xG 모델을 다시 학습한다.

Usage: PYTHONPATH=. .venv/bin/python tools/fc_xg_fit.py
출력된 WEIGHTS를 imposter_finder/analysis/fc_xg.py에 붙여 넣는다.
"""
import json
import sqlite3

from imposter_finder.analysis.fc_xg import fit, shot_xg
from imposter_finder.config import load_settings


def main() -> None:
    db = sqlite3.connect(load_settings().local_database_path)
    matches = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM fc_matches")]
    sides = [side for m in matches for side in m["matchInfo"] if side.get("player")]
    shots = [shot for side in sides for shot in side["shootDetail"]]
    weights, loss = fit(shots)
    goals = sum(shot["result"] == 3 for shot in shots)
    expected = sum(shot_xg(shot, weights) for shot in shots)
    print(f"{len(matches)} matches · {len(shots)} shots · {goals} goals · xG {expected:.1f} · log loss {loss:.3f}")
    print("WEIGHTS = (" + ", ".join(f"{w:.3f}" for w in weights) + ")")


if __name__ == "__main__":
    main()

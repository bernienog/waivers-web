"""Migra claims.json + logs/*.json -> SQLite. Solo lee archivos. Uso:
  python -m core.migrate [--week 2]
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.db import connect  # noqa: E402
from core.week import current_week_default  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, default=current_week_default())
    args = ap.parse_args()
    cx = connect()
    n = 0
    # claims.json legacy (formato viejo: add/drop/bid)
    path = os.path.join(ROOT, "claims.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for c in json.load(f):
                cx.execute(
                    "INSERT INTO claims (player_id, league_id, week, base_bid,"
                    " league_bid, drop_player_id, status, user_status)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (c.get("add"), c.get("league_id"), args.week,
                     c.get("bid", 0), c.get("bid", 0), c.get("drop", ""),
                     "draft", "draft"))
                n += 1
    # logs/*.json (auditoria de submits reales)
    for lp in sorted(glob.glob(os.path.join(ROOT, "logs", "*.json"))):
        with open(lp, encoding="utf-8") as f:
            e = json.load(f)
        v = e.get("vars", {})
        cx.execute(
            "INSERT INTO claims (player_id, league_id, week, league_bid,"
            " drop_player_id, status, user_status, transaction_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (v.get("add"), e.get("league_id"), args.week, v.get("bid", 0),
             v.get("drop", ""), "submitted" if e.get("ok") else "failed",
             "ready", e.get("transaction_id")))
        n += 1
    cx.commit()
    print(f"[migrate] {n} claims -> {cx.execute('SELECT COUNT(*) FROM claims').fetchone()[0]} total")


if __name__ == "__main__":
    main()

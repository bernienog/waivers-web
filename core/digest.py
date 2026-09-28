"""Morning digest (Fase D real, firma fijada en A).

Solo-API-publica: compara claims submitted contra /transactions/{week}.
Match correcto: t.adds[player_id] == TU roster_id (no league_id).
Solo lee Sleeper; escribe unicamente la DB local.
"""

import time

from . import sleeper_public as pub
from .models import LOST, UNKNOWN, WON


def resolve_claim(league_id: str, roster_id: int, player_id: str,
                  drop_id: str, week: int) -> str:
    txs = pub.get_transactions(league_id, week)
    for t in txs:
        if (t.get("type") == "waiver"
                and str((t.get("adds") or {}).get(str(player_id))) == str(roster_id)
                and str((t.get("drops") or {}).get(str(drop_id))) == str(roster_id)):
            return WON
    for t in txs:
        if t.get("type") == "waiver" and str(player_id) in (t.get("adds") or {}):
            return LOST
    return UNKNOWN


def run_digest(week: int, cx) -> dict:
    rows = cx.execute(
        "SELECT * FROM claims WHERE status='submitted' AND week=?", (week,)).fetchall()
    counts = {WON: 0, LOST: 0, UNKNOWN: 0}
    for r in rows:
        res = resolve_claim(r["league_id"], _roster_of(cx, r), r["player_id"],
                            r["drop_player_id"], week)
        counts[res] += 1
        cx.execute("UPDATE claims SET status='resolved', resolution=?,"
                   " resolved_at=? WHERE id=?",
                   (res, int(time.time() * 1000), r["id"]))
    cx.commit()
    return {"processed": len(rows), **counts}


def _roster_of(cx, row) -> int:
    r = cx.execute("SELECT roster_id FROM rosters_cache WHERE league_id=?",
                   (row["league_id"],)).fetchone()
    if r:
        return int(r["roster_id"])
    # Fallback: cache is never populated (manual moves bypass DB).
    # Resolve live so WON can still match; 0 = unknown.
    import os
    uid = os.environ.get("SLEEPER_USER_ID", "")
    try:
        rid = pub.find_roster_id(row["league_id"], uid)
        return int(rid) if rid else 0
    except Exception:
        return 0

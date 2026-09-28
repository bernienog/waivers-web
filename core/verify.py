"""Tier-1 verify: reconcilia claims submitted contra Sleeper (solo lectura
remota; escribe UNICAMENTE la DB local).

Por cada claim submitted con transaction_id:
- tid sigue en pending live -> checked_at (sigue vivo, no tocar)
- match en public complete/failed (semanas W y W-1; nuestra numeracion
  corre +1 vs Sleeper) -> resolved won/lost + tid
- pending VACIO en la liga + tid aceptado + sin rastro -> failed
  (vanished pre-process, patron Shough: cancelado antes de procesar no
  deja rastro; si el slate entero proceso, no hay nada que esperar)
- sin match, pending no vacio y viejo (>SETTLE_H) -> failed (idem)
- sin match, pending no vacio y joven -> checked_at (muy pronto para
  sentenciar)

Ligas cuyo pending fetch falla se saltan intactas: jamas marcar failed
a ciegas. No resuelve UNKNOWN prematuro (eso es del digest morning-after).
"""

import time
from concurrent.futures import ThreadPoolExecutor

from . import sleeper_private as priv
from . import sleeper_public as pub
from .db import DEFAULT_PATH, connect
from .digest import _roster_of

SETTLE_H = 6


def _ms(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n * 1000 if n < 1_000_000_000_000 else n


def _tx_bid(t):
    """waiver_bid del tx público, si lo trae. Solo bids propios (won o
    failed propio): el complete ajeno trae el bid del ganador."""
    b = (t.get("settings") or {}).get("waiver_bid")
    if isinstance(b, bool):
        return None
    try:
        return int(b) if b is not None else None
    except (TypeError, ValueError):
        return None


def _tx_note(t):
    """Veredicto verbatim de Sleeper (metadata.notes): 'claimed by
    another owner', 'too many players', 'processed successfully'..."""
    n = (t.get("metadata") or {}).get("notes")
    return str(n) if n else None


def _winner_uid(all_txs, player_id, roster_id):
    """user_id del ganador: complete del mismo jugador en otro roster.
    None si no hay (o si soy yo)."""
    for t in all_txs:
        if t.get("type") != "waiver" or t.get("status") != "complete":
            continue
        adds = t.get("adds") or {}
        if (str(player_id) in adds
                and str(adds.get(str(player_id))) != str(roster_id)):
            return t.get("creator")
    return None


def _match(league_id, roster_id, player_id, drop_id, weeks, _txs=None):
    """(resolution|None, tid|None, bid|None, note|None, winner_uid|None).
    failed propio (adds==mi roster en failed) y complete ajeno cuentan
    como lost. Lo propio se chequea en TODAS las semanas antes que lo
    ajeno (un complete mío en W-1 gana sobre un ajeno en W).
    _txs: {week: [tx]} pre-fetcheado — antes cada claim re-bajaba las
    mismas 2 páginas de transactions (5 submitted = 5x lo mismo)."""
    all_txs: list = []
    for wk in weeks:
        if _txs is not None and int(wk) in _txs:
            all_txs.extend(_txs[int(wk)])
        else:
            try:
                all_txs.extend(pub.get_transactions(league_id, int(wk)))
            except Exception:
                continue
    for t in all_txs:
        if t.get("type") != "waiver":
            continue
        adds = t.get("adds") or {}
        drops = t.get("drops") or {}
        st = t.get("status")
        if str(adds.get(str(player_id))) == str(roster_id):
            if st == "complete" and (
                    not drop_id or str(drops.get(str(drop_id))) == str(roster_id)):
                return "won", t.get("transaction_id"), _tx_bid(t), _tx_note(t), None
            if st == "failed":
                return ("lost", t.get("transaction_id"), _tx_bid(t),
                        _tx_note(t),
                        _winner_uid(all_txs, player_id, roster_id))
    for t in all_txs:
        if t.get("type") != "waiver" or t.get("status") != "complete":
            continue
        adds = t.get("adds") or {}
        if (str(player_id) in adds
                and str(adds.get(str(player_id))) != str(roster_id)):
            return "lost", t.get("transaction_id"), None, None, t.get("creator")
    return None, None, None, None, None


def _display_winner(league_id, winner_uid):
    """Display name del ganador (cache 24h). None si no se resuelve."""
    if not winner_uid:
        return None
    try:
        return pub.get_league_users(league_id).get(str(winner_uid))
    except Exception:
        return None


def _verify_league(db_path, rows, now, roster_id="__all__"):
    """Worker por liga con conexión propia (SQLite no se comparte entre
    hilos). Lee pendings una vez, liquida sus filas, commit propio.
    roster_id: scope del pull (mi roster = mismo cache que /api/pending;
    "__all__" = toda la liga, para scripts). Mis submitted son míos: el
    still-pending check es idéntico en ambos scopes."""
    cx = connect(db_path)
    lid = rows[0]["league_id"]
    part = {"checked": 0, "won": 0, "lost": 0, "vanished": 0,
            "skipped": [], "details": []}
    scope = None if roster_id == "__all__" else roster_id
    try:
        # Cache 30s compartido con /api/pending: el Hub pide verify+pending
        # seguidos y era el mismo pull dos veces.
        pend_tids = {t.get("transaction_id")
                     for t in priv.list_pending_cached(lid, scope)}
    except Exception as e:
        part["skipped"].append({"league_id": lid, "reason": str(e)[:200]})
        cx.close()
        return part
    # Una bajada por semana tocada (W y W-1 de todas las filas), no una
    # por claim: las páginas se comparten entre filas de la misma liga.
    weeks = sorted({int(r["week"]) for r in rows}
                   | {int(r["week"]) - 1 for r in rows})
    txcache: dict = {}
    for wk in weeks:
        try:
            txcache[wk] = pub.get_transactions(lid, wk)
        except Exception:
            txcache[wk] = []  # == el continue de antes: semana sin datos
    for r in rows:
        rid = _roster_of(cx, r)
        tid = str(r["transaction_id"])
        if tid in pend_tids:
            cx.execute("UPDATE claims SET checked_at=? WHERE id=?",
                       (now, r["id"]))
            part["checked"] += 1
            part["details"].append({"id": r["id"], "what": "still-pending"})
            continue
        res, mtid, bid, note, winner_uid = _match(
            lid, rid, r["player_id"], r["drop_player_id"],
            (r["week"], r["week"] - 1), _txs=txcache)
        if res in ("won", "lost"):
            # El bid público manda sobre el de creación (edits post-send
            # y redondeos): solo pisa cuando el tx lo trae y es propio
            # (el complete ajeno trae el bid del ganador: bid=None ahí).
            # note/winner: veredicto verbatim de Sleeper + quién se lo
            # llevó (display name, cache 24h). COALESCE: jamás pisa con
            # NULL lo ya sabido.
            winner = _display_winner(lid, winner_uid)
            cx.execute("UPDATE claims SET status='resolved', resolution=?,"
                       " resolved_at=?, transaction_id=?, checked_at=?,"
                       " league_bid=COALESCE(?, league_bid),"
                       " note=COALESCE(?, note),"
                       " winner=COALESCE(?, winner)"
                       " WHERE id=?",
                       (res, now, mtid or tid, now, bid, note, winner,
                        r["id"]))
            part[res] += 1
            part["details"].append({"id": r["id"], "what": res,
                                   "tid": mtid or tid})
            continue
        sent = _ms(r["sent_at"])
        age_h = (now - sent) / 3600000 if sent else 999.0
        if not pend_tids:
            # Morning-after: el slate entero proceso (pending vacio), el
            # tid fue aceptado anoche y no hay rastro en public. Concluir
            # vanished sin hedge de edad: no hay nada que esperar.
            reason = ("verify: slate procesado (pending vacio), tid aceptado"
                      " sin rastro en public (vanished pre-process).")
            old = False
        elif age_h > SETTLE_H:
            reason = ("verify: tid fuera de pending y sin rastro en"
                      " public (vanished pre-process).")
            old = True
        else:
            cx.execute("UPDATE claims SET checked_at=? WHERE id=?",
                       (now, r["id"]))
            part["checked"] += 1
            part["details"].append({"id": r["id"], "what": "too-young"})
            continue
        cx.execute("UPDATE claims SET status='failed', checked_at=?,"
                   " error_text=? WHERE id=?", (now, reason, r["id"]))
        part["vanished"] += 1
        part["details"].append({"id": r["id"],
                                "what": "vanished-empty" if not old else "vanished"})
    cx.commit()
    cx.close()
    return part


def run_backfill(cx, league_id=None, db_path=None):
    """One-shot hacia atrás: re-sincroniza bid + nota + ganador en filas
    ya `resolved` desde el rastro público (el heal de steady-state solo
    toca submitted). Solo lectura remota; escribe únicamente cuando el tx
    trae dato propio que difiere. Retorna {checked, updated, details}."""
    db_path = db_path or DEFAULT_PATH
    q = ("SELECT * FROM claims WHERE status='resolved'"
         " AND resolution IN ('won','lost')"
         " AND transaction_id IS NOT NULL AND transaction_id != ''")
    args: list = []
    if league_id:
        q += " AND league_id=?"
        args.append(league_id)
    rows = [dict(r) for r in cx.execute(q, args).fetchall()]
    out = {"checked": 0, "updated": 0, "details": []}
    by_league: dict = {}
    for r in rows:
        by_league.setdefault(r["league_id"], []).append(r)
    for lid, rs in sorted(by_league.items()):
        weeks = sorted({int(r["week"]) for r in rs}
                       | {int(r["week"]) - 1 for r in rs})
        txcache: dict = {}
        for wk in weeks:
            try:
                txcache[wk] = pub.get_transactions(lid, wk)
            except Exception:
                txcache[wk] = []
        for r in rs:
            out["checked"] += 1
            rid = _roster_of(cx, r)
            res, _mtid, bid, note, winner_uid = _match(
                lid, rid, r["player_id"], r["drop_player_id"],
                (r["week"], r["week"] - 1), _txs=txcache)
            winner = _display_winner(lid, winner_uid)
            patch: dict = {}
            if bid is not None and int(bid) != int(r["league_bid"] or 0):
                patch["league_bid"] = int(bid)
            if note and note != (r["note"] or None):
                patch["note"] = note
            if winner and winner != (r["winner"] or None):
                patch["winner"] = winner
            if patch:
                cx.execute("UPDATE claims SET {} WHERE id=?".format(
                    ", ".join(f"{k}=?" for k in patch)),
                    (*patch.values(), r["id"]))
                out["updated"] += 1
                out["details"].append({"id": r["id"], **patch,
                                       "resolution": res})
    cx.commit()
    return out


def run_verify(cx, league_id=None, db_path=None, now=None,
             roster_ids=None):
    now = int(now if now is not None else time.time() * 1000)
    db_path = db_path or DEFAULT_PATH
    q = ("SELECT * FROM claims WHERE status='submitted'"
         " AND transaction_id IS NOT NULL AND transaction_id != ''")
    args: list = []
    if league_id:
        q += " AND league_id=?"
        args.append(league_id)
    rows = [dict(r) for r in cx.execute(q, args).fetchall()]
    out = {"checked": 0, "won": 0, "lost": 0, "vanished": 0,
           "skipped": [], "details": []}
    by_league: dict = {}
    for r in rows:
        by_league.setdefault(r["league_id"], []).append(r)
    if not by_league:
        return out
    roster_ids = roster_ids or {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        parts = list(ex.map(
            lambda kv: _verify_league(
                db_path, kv[1], now,
                roster_ids.get(kv[0], "__all__")),
            sorted(by_league.items())))
    for p in parts:
        for k in ("checked", "won", "lost", "vanished"):
            out[k] += p[k]
        out["skipped"].extend(p["skipped"])
        out["details"].extend(p["details"])
    return out

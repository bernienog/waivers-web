"""ETL FantasyPros waiver-wire (consenso ECR) -> tabla local fp_wire.

Solo lee: fetch de la pagina publica + mapa local de Sleeper.
El SOS (var sosData) se ignora a proposito: ruido para el wire semanal.
Columnas que se guardan: rank, jugador, owned%, matchup, tag FAAB, nota.
"""

import json
import re
import time

URL = "https://www.fantasypros.com/nfl/rankings/waiver-wire-half-point-ppr-overall.php"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(s):
    """Minusculas, sin sufijos (Jr/III), sin puntuacion. Ambos lados igual."""
    s = _SUFFIX.sub(" ", (s or "").lower())
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_pos(p):
    p = (p or "").upper()
    return "DEF" if p == "DST" else p


def fetch_html(url=URL, timeout=30):
    import requests
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.text


def _extract_balanced(html, marker):
    """Extrae el objeto JSON que sigue al marker, balanceando llaves
    (ignorando strings). El HTML trae var ecrData = {...}; y var sosData
    aparte: solo se toca ecrData."""
    i = html.find(marker)
    if i < 0:
        raise ValueError(f"marker {marker!r} no encontrado")
    j = html.find("{", i)
    depth = 0
    instr = False
    esc = False
    for k in range(j, len(html)):
        ch = html[k]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        elif ch == '"':
            instr = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[j:k + 1]
    raise ValueError("JSON desbalanceado tras marker")


def extract_ecr(html):
    """-> {week, year, scoring, players: [raw...]}. Sin SOS, sin expertos."""
    data = json.loads(_extract_balanced(html, "var ecrData"))
    return {"week": int(data.get("week") or 0),
            "year": str(data.get("year") or ""),
            "scoring": str(data.get("scoring") or ""),
            "players": data.get("players") or []}


def clean_row(r):
    """Solo senal de la tabla Overview: rank, jugador, pos-rank (WR3),
    bye, owned, tag. SOS/bye-detail/min-max: ruido, fuera."""
    try:
        ave = float(r.get("rank_ave")) if r.get("rank_ave") not in (None, "") else None
    except (TypeError, ValueError):
        ave = None
    try:
        owned = float(r.get("player_owned_avg")) if r.get("player_owned_avg") not in (None, "") else None
    except (TypeError, ValueError):
        owned = None
    try:
        bye = int(r.get("player_bye_week")) if r.get("player_bye_week") not in (None, "") else None
    except (TypeError, ValueError):
        bye = None
    name = r.get("player_name") or ""
    return {"fp_id": int(r.get("player_id")),
            "name": name,
            "norm": norm_name(name),
            "pos": norm_pos(r.get("player_position_id")),
            "team": (r.get("player_team_id") or "").upper(),
            "rank_ecr": r.get("rank_ecr"),
            "rank_ave": ave,
            "pos_rank": r.get("pos_rank") or "",
            "bye": bye,
            "owned_avg": owned,
            "opp": r.get("player_opponent") or "",
            "tag": r.get("tag") or "",
            "note": r.get("note") or ""}


def match_sleeper(rows, pmap):
    """Cruza por nombre normalizado + pos + equipo. Ambiguo o sin
    candidato -> sleeper_id None (la UI lo muestra, sin bloquear)."""
    idx: dict = {}
    for pid, v in (pmap or {}).items():
        n = norm_name(v.get("name") or "")
        if n:
            idx.setdefault(n, []).append(
                (str(pid), (v.get("pos") or "").upper(),
                 (v.get("team") or "").upper()))
    for r in rows:
        cands = [c for c in idx.get(r["norm"], []) if c[1] == r["pos"]]
        team_ok = [c for c in cands if c[2] == r["team"]]
        r["sleeper_id"] = team_ok[0][0] if len(team_ok) == 1 else None
    return rows


def run_import(cx, pmap, html=None):
    """Fetch -> clean -> match -> replace snapshot. Devuelve resumen."""
    if html is None:
        html = fetch_html()
    ecr = extract_ecr(html)
    rows = match_sleeper([clean_row(r) for r in ecr["players"]], pmap)
    now = int(time.time() * 1000)
    cx.execute("DELETE FROM fp_wire")
    for r in rows:
        cx.execute(
            "INSERT INTO fp_wire (fp_id, name, pos, team, sleeper_id,"
            " rank_ecr, rank_ave, pos_rank, bye, owned_avg, opp, tag,"
            " note, week, scoring, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["fp_id"], r["name"], r["pos"], r["team"], r["sleeper_id"],
             r["rank_ecr"], r["rank_ave"], r["pos_rank"], r["bye"],
             r["owned_avg"], r["opp"], r["tag"], r["note"],
             ecr["week"], ecr["scoring"], now))
    cx.commit()
    matched = sum(1 for r in rows if r["sleeper_id"])
    return {"week": ecr["week"], "scoring": ecr["scoring"],
            "imported": len(rows), "matched": matched,
            "unmatched": [r["name"] for r in rows if not r["sleeper_id"]]}

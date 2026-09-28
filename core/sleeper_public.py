"""Capa publica solo-lectura (api.sleeper.app). Sin auth."""

import os
import time

from .fetch import sleeper_get

BASE = "https://api.sleeper.app/v1"

# /projections NO cuelga de api.sleeper.app/v1: ese host responde otra
# forma (lista corta sin player_id) y el parse daba 0 filas. La UI usa el
# dominio .com plano, que devuelve las ~3300 filas de la semana.
PROJ_BASE = "https://api.sleeper.com"


def get_user(username):
    """Ficha pública por username (para resolver user_id sin auth).
    Retorna {} si no existe."""
    r = sleeper_get(f"{BASE}/user/{username}", timeout=20)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json() or {}


_PROJ_REST_CACHE: dict = {}  # (season, week) -> (epoch_s, {pid: {...}})
PROJ_REST_TTL = 6 * 3600  # espejo del TTL que tenía el batch privado

_SCORING_CACHE: dict = {}  # ("scoring", league_id) -> (epoch_s, {stat: w})
SCORING_TTL = 24 * 3600


_LEAGUE_CACHE: dict = {}  # league_id -> (epoch_s, {league json})
_LEAGUE_TTL = 6 * 3600  # el /league trae settings + scoring_settings juntos


def get_league_full(league_id, fresh=False):
    """El JSON completo de la liga (cache 6h). Trae `settings` (waiver_day,
    waiver_clear_days...) y `scoring_settings` de UNA llamada: antes
    hacíamos dos GET al mismo endpoint."""
    import time as _time
    key = str(league_id)
    now = _time.time()
    hit = _LEAGUE_CACHE.get(key)
    if hit and not fresh and now - hit[0] < _LEAGUE_TTL:
        return hit[1]
    r = sleeper_get(f"{BASE}/league/{league_id}", timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    j = r.json() or {}
    _LEAGUE_CACHE[key] = (now, j)
    return j


def get_scoring_settings(league_id, fresh=False):
    """scoring_settings de la liga (CLAVE TOP-LEVEL, no dentro de settings).
    Es lo que usa la UI para pasar los pts proyectados a puntos de tu
    liga (NUCLEAR: 2.0/rec, 0.05/yd, 6/TD, 0.5/cmp, 1.0/rush att).
    Cache 24h: los settings cambian muy rare veces; fresh=1 lo revienta."""
    import time as _time
    key = ("scoring", str(league_id))
    now = _time.time()
    hit = _SCORING_CACHE.get(key)
    if hit and not fresh and now - hit[0] < SCORING_TTL:
        return hit[1]
    sc = get_league_full(league_id, fresh=fresh).get("scoring_settings") or {}
    _SCORING_CACHE[key] = (now, sc)
    return sc


def get_projections_week(season, week):
    """Proyecciones semanales vía REST pública (la que usa la propia UI).
    Sin JWT, una llamada (~3300 jugadores), cache 6h. Retorna {pid:
    {proj_ppr, proj_half, proj_std, opp}}. Lanza si Sleeper falla: el
    caller decide (la pestaña Proy falla fuerte, nunca degrada a rank
    en silencio). Solo se cachea lo no-vacío: un fetch malo jamás
    envenena las 6h siguientes."""
    import time as _time
    key = (str(season), int(week))
    now = _time.time()
    hit = _PROJ_REST_CACHE.get(key)
    if hit and now - hit[0] < PROJ_REST_TTL:
        return hit[1]
    # Params EXACTOS de la UI (HAR): requests codifica [] como %5B%5D y
    # Sleeper lee cero posiciones. Query literal, filtro en local.
    # OJO el host: /projections NO vive bajo /v1. Con /v1 la API responde
    # 200 con otra forma (lista de 7630 items sin player_id) y el parse
    # daba 0 filas. El host plano es el que usa la UI (3305 filas).
    pos = "&".join(f"position[]={p}" for p in
                   ["DEF", "FLEX", "K", "QB", "RB", "TE", "WR"])
    url = (f"{PROJ_BASE}/projections/nfl/{season}/{int(week)}"
           f"?season_type=regular&{pos}&order_by=ppr")
    r = sleeper_get(url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    data = r.json()
    rows = data if isinstance(data, list) else (data or {}).get("rows") or []
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("player_id") or "")
        if not pid:
            continue
        st = row.get("stats") or {}
        out[pid] = {"proj_ppr": st.get("pts_ppr"),
                    "proj_half": st.get("pts_half_ppr"),
                    "proj_std": st.get("pts_std"),
                    # Stats crudas: el `pts_ppr` del payload es scoring
                    # estándar, no PPR. Los puntos de fantasy los calcula
                    # core/scoring.py desde aquí (ver nota de scoring).
                    "stats": st,
                    "opp": row.get("opponent")}
    if out:
        _PROJ_REST_CACHE[key] = (now, out)
    return out


def get_leagues(user_id, season):
    r = sleeper_get(f"{BASE}/user/{user_id}/leagues/nfl/{season}", timeout=20)
    r.raise_for_status()
    return r.json()


def get_rosters(league_id):
    r = sleeper_get(f"{BASE}/league/{league_id}/rosters", timeout=20)
    r.raise_for_status()
    return r.json()


def get_transactions(league_id, week):
    r = sleeper_get(f"{BASE}/league/{league_id}/transactions/{int(week)}",
                    timeout=20)
    r.raise_for_status()
    return r.json() or []


def get_league(league_id):
    r = sleeper_get(f"{BASE}/league/{league_id}", timeout=20)
    r.raise_for_status()
    return r.json()


def get_league_bundle(league_id, user_id):
    """Un par de llamadas (league + rosters) reutilizable por todo lo que
    una ruta necesite de una liga: modo, presupuesto, roster, rostered,
    roster_id, slots. Sin esto cada helper refetcheaba lo mismo (5 calls
    por _league_row, 6 por quick).

    SIEMPRE en vivo a propósito: availability, los writers y los re-checks
    pre-send dependen de que esto NO se cachee. Para pintar la lista de
    ligas está `get_bundle_display()`."""
    lg = get_league(league_id)
    rosters = get_rosters(league_id)
    mine = None
    for r in rosters:
        if str(r.get("owner_id")) == str(user_id):
            mine = r
            break
    return {"league": lg, "rosters": rosters, "roster": mine}


# Cache SOLO de display para /api/leagues (record, saldo, slots libres,
# modo, semana). TTL corto porque los rosters cambian con cada waiver; los
# campos que deciden (availability, envío, clears) nunca leen de aquí.
_BUNDLE_DISPLAY: dict = {}
_BUNDLE_DISPLAY_TTL = 600  # 10 min


def get_bundle_display(league_id, user_id, fresh=False):
    """Bundle cacheado para pintar. `fresh=True` lo salta (el ↻ global)."""
    if not fresh:
        hit = _BUNDLE_DISPLAY.get(league_id)
        if hit and (time.time() - hit[0]) < _BUNDLE_DISPLAY_TTL:
            return hit[1]
    b = get_league_bundle(league_id, user_id)
    _BUNDLE_DISPLAY[league_id] = (time.time(), b)
    return b


def bust_bundle_display(league_id=None):
    """Lo vacían los writers: tras enviar/cancelar, el roster y el saldo de
    esa liga ya cambiaron y el cache de display quedó viejo."""
    if league_id is None:
        _BUNDLE_DISPLAY.clear()
    else:
        _BUNDLE_DISPLAY.pop(league_id, None)


def cache_stats():
    """Telegrafía del cache de display, para /api/health."""
    return {"display_entries": len(_BUNDLE_DISPLAY),
            "display_ttl_s": _BUNDLE_DISPLAY_TTL}


# roster_id es estable toda la temporada (solo cambia si sales/entras de
# la liga): memo de proceso, evita un get_rosters por refresh.
_ROSTER_ID_MEMO: dict = {}


def waiver_mode(league_id, _league=None):
    """Detecta FAAB vs claim normal desde settings publicos.
    waiver_type==2 -> FAAB (validado con tu Juantasy).
    0/1 -> claim por prioridad (sin bid).
    Retorna dict {mode, waiver_type, budget, bid_min}.
    _league: json ya fetcheado (bundle) para no repetir el GET."""
    lg = _league if _league is not None else get_league(league_id)
    s = lg.get("settings", {}) or {}
    wt = int(s.get("waiver_type", 0))
    if wt == 2:
        mode = "faab"
    else:
        mode = "claim"
    return {
        "mode": mode,
        "waiver_type": wt,
        "league_name": lg.get("name"),
        "budget": s.get("waiver_budget"),
        "bid_min": s.get("waiver_bid_min", 0),
    }


def remaining_budget(league_id, user_id, _league=None, _roster=None):
    lg = _league if _league is not None else get_league(league_id)
    budget = (lg.get("settings", {}) or {}).get("waiver_budget", 0)
    roster = _roster if _roster is not None else get_my_roster(league_id, user_id)
    used = ((roster or {}).get("settings", {}) or {}).get("waiver_budget_used", 0)
    return int(budget or 0) - int(used or 0)


def find_roster_id(league_id, user_id, _rosters=None):
    """Resuelve roster_id (el '7' hardcodeado) para no repetirlo a mano.
    Memo de proceso: estable toda la temporada."""
    key = (str(league_id), str(user_id))
    if key in _ROSTER_ID_MEMO:
        return _ROSTER_ID_MEMO[key]
    rosters = _rosters if _rosters is not None else get_rosters(league_id)
    rid = None
    for roster in rosters:
        if str(roster.get("owner_id")) == str(user_id):
            rid = int(roster["roster_id"])
            break
    if rid is not None:
        _ROSTER_ID_MEMO[key] = rid
    return rid


def roster_space(league, roster):
    """Slots libres reales, UNA sola fórmula en todo el repo.
    Taxi/IR viven dentro de players[] pero ocupan sus propios slots:
    se descuentan (acotados a su capacidad; el excedente come banca).
    Puro, sin red. Caso extremo real: Dynasty 25/29/taxi-2/IR-3 -> free 1.
    """
    positions = (league or {}).get("roster_positions") or []
    settings = (league or {}).get("settings") or {}
    players = (roster or {}).get("players") or []
    taxi = (roster or {}).get("taxi") or []
    reserve = (roster or {}).get("reserve") or []
    taxi_slots = int(settings.get("taxi_slots") or 0)
    res_slots = int(settings.get("reserve_slots") or 0)
    used = (len(players) - min(len(taxi), taxi_slots)
            - min(len(reserve), res_slots))
    return {"capacity": len(positions),
            "used": max(0, used),
            "free": max(0, len(positions) - used),
            "taxi_capacity": taxi_slots,
            "taxi_used": len(taxi),
            "taxi_free": max(0, taxi_slots - len(taxi)),
            "reserve_capacity": res_slots,
            "reserve_used": len(reserve),
            "reserve_free": max(0, res_slots - len(reserve))}


def get_my_roster(league_id, user_id, _rosters=None):
    rosters = _rosters if _rosters is not None else get_rosters(league_id)
    for roster in rosters:
        if str(roster.get("owner_id")) == str(user_id):
            return roster
    return None


def get_rostered_ids(league_id, _rosters=None):
    """Todos los player_ids con dueño en la liga. El add NO debe estar aqui."""
    taken = set()
    for roster in (_rosters if _rosters is not None else get_rosters(league_id)):
        for pid in (roster.get("players") or []):
            taken.add(str(pid))
    return taken


_MAP_CACHE: dict = {}  # cache_path -> (mtime_ns, data)

# Miembros de la liga: user_id -> display_name. Cambia poco; TTL largo.
# Solo se usa al resolver (ganador de un lost) — 1 call/liga/día.
_USERS_CACHE: dict = {}  # league_id -> (epoch_s, {user_id: name})
USERS_TTL = 24 * 3600


def get_league_users(league_id):
    """Mapa user_id -> display_name de la liga (para nombrar al ganador
    de un lost). Cache 24h en memoria."""
    import time as _time
    now = _time.time()
    key = str(league_id)
    hit = _USERS_CACHE.get(key)
    if hit and now - hit[0] < USERS_TTL:
        return hit[1]
    r = sleeper_get(f"{BASE}/league/{league_id}/users", timeout=20)
    r.raise_for_status()
    m = {str(u.get("user_id")): (u.get("display_name") or u.get("username")
                                 or str(u.get("user_id")))
         for u in (r.json() or [])}
    _USERS_CACHE[key] = (now, m)
    return m


def _mtime(path):
    import os as _os
    try:
        return _os.path.getmtime(path)
    except OSError:
        return -1


def _players_cache_path():
    """Cache del mapa de jugadores (~10MB) en la carpeta de DATOS, no en
    la de instalación: sobrevive reinstalls y no ensucia el program dir.
    WAIVERS_DATA_DIR (Tauri) o repo (dev)."""
    data = os.environ.get("WAIVERS_DATA_DIR", "")
    if not data:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data = root
    return os.path.join(data, "players_nfl.json")


def get_players_map(cache_path=None):
    """Mapa player_id -> {name, pos, team, rank, inj}. Cache local (API pesa ~10MB).
    rank = search_rank de Sleeper (su propio orden de lista).
    inj = injury_status (Q/O/IR/...) para matizar el rank anual.
    Segunda capa: memo en memoria por mtime - antes se re-parseaba el
    JSON de 10MB en CADA llamada (claims_list lo pedía 5x por refresh,
    search una vez por tecla)."""
    import json
    import os
    cache_path = cache_path or _players_cache_path()
    if (cache_path in _MAP_CACHE
            and _MAP_CACHE[cache_path][0] == _mtime(cache_path)):
        return _MAP_CACHE[cache_path][1]
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        # Cache vieja sin rank+inj -> refetch una vez y listo.
        sample = next(iter(cached.values()), {})
        if cached and "rank" in sample and "inj" in sample:
            _MAP_CACHE[cache_path] = (_mtime(cache_path), cached)
            return cached
    r = sleeper_get(f"{BASE}/players/nfl", timeout=60)
    r.raise_for_status()
    raw = r.json()
    slim = {}
    for pid, p in raw.items():
        name = (p.get("full_name")
                or (str(p.get("first_name", "")) + " " + str(p.get("last_name", ""))).strip())
        if not name:
            continue
        slim[str(pid)] = {
            "name": name,
            "pos": p.get("position"),
            "team": p.get("team"),
            "rank": p.get("search_rank"),
            "inj": p.get("injury_status"),
        }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(slim, f)
    _MAP_CACHE[cache_path] = (_mtime(cache_path), slim)
    return slim


def search_players(query, limit=10, cache_path="players_nfl.json"):
    q = query.lower()
    m = get_players_map(cache_path)
    hits = [(pid, v) for pid, v in m.items() if q in v["name"].lower()]
    return hits[:limit]

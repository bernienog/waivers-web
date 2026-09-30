"""Capa publica solo -lectura (api.sleeper.app). Sin auth."""

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
    en silencio). solo  se cachea lo no-vacío: un fetch malo jamás
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


# Cuando el pool de free agents se destraba. La senal de verdad es el
# status_updated del ultimo lote de waivers (ver
# sleeper_private.newest_waiver_resolution): no hay constante ni margen, se
# lee el evento. Esta capa solo cachea para no pegarle a Sleeper dos veces
# por render.
_WAIVER_TTL = 30  # s. Corto a proposito: la ventana tiene que abrir al toque.
_WAIVER_CACHE: dict = {}  # league_id -> (epoch_s, ms|0)


def newest_waiver_cached(league_id, fresh=False, _now=None):
    """Ultimo lote de waivers resuelto por la liga, en ms. 0 = nunca hubo.

    `fresh=True` se usa en la PRIMERA carga de Mis Ligueas para que el W o el
    + salga bien desde el primer render: con 30s de cache, un usuario que
    acaba de abrir la app veria el estado anterior hasta que expire. Despues
    del primer render 30s alcanza y evita un poke por liga cada vez que se
    repinta una fila.

    Nunca lanza: si falta el JWT o Sleeper falla se devuelve el valor viejo
    (o 0) y `waiver_window` cae a la cadencia, que es el camino conocido.
    """
    now = _now if _now is not None else time.time()
    hit = _WAIVER_CACHE.get(str(league_id))
    if hit and not fresh and now - hit[0] < _WAIVER_TTL:
        return hit[1]
    try:
        from . import sleeper_private as priv
        ms = priv.newest_waiver_resolution(league_id)
    except Exception:
        ms = hit[1] if hit else 0
    _WAIVER_CACHE[str(league_id)] = (now, ms)
    return ms


def bust_waiver_cache(league_id=None):
    """Reventa la senal de resolucion (writers: send/cancel/update/reorder)."""
    if league_id is None:
        _WAIVER_CACHE.clear()
    else:
        _WAIVER_CACHE.pop(str(league_id), None)


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


# Cache solo  de display para /api/leagues (record, saldo, slots libres,
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


def clear_process_caches():
    """Vacía los memos de proceso. Los llama la API cuando cambia la
    cuenta de Sleeper: son claves por league_id, no por cuenta, así que
    un cambio de usuario deja datos del anterior en memoria hasta que
    expire el TTL."""
    _BUNDLE_DISPLAY.clear()
    _ROSTER_ID_MEMO.clear()


# roster_id es estable toda la temporada (solo  cambia si sales/entras de
# la liga): memo de proceso, evita un get_rosters por refresh.
_ROSTER_ID_MEMO: dict = {}


WAIVER_TZ_DEFAULT = "America/New_York"


WAIVER_TZ_DEFAULT = "UTC"


def _waiver_tz():
    """Marco en el que cuenta el día y la hora de waivers.

    UTC, no el reloj del usuario ni una zona inventada. Calibrado con un
    dato concreto: en México (CST, UTC-6, sin DST) los waivers corren a
    la 1:00 AM del miércoles, que es 07:00 UTC. 23:24 del martes + 97 min
    = 07:00 UTC del miércoles: el momento exacto. Con el reloj local la
    app contaba el día corrido y decía "libre" a las 00:01 mientras la
    liga seguía bloqueada.

    Override por .env `WAIVERS_WAIVER_TZ` si una liga corre con otro marco.
    """
    import datetime as _dt
    name = os.environ.get("WAIVERS_WAIVER_TZ", "").strip() or WAIVER_TZ_DEFAULT
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return _dt.timezone.utc


def _pacific_offset(at=None):
    """Offset de US/Pacific a ese instante: -7 con DST, -8 sin ella.

    El DST de USA corre del segundo domingo de marzo al primer domingo de
    noviembre, y no es el de Mexico: Mexico no hace DST, USA si. Por eso
    esto se resuelve con la zona de verdad y no con una constante.
    """
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        la = ZoneInfo("America/Los_Angeles")
        when = at or _dt.datetime.now(_dt.timezone.utc)
        off = when.astimezone(la).utcoffset()
        # utcoffset va con SIGNO: PDT es -7. Se devuelve tal cual para que
        # `utc = hora_pacifico - offset` sume (0 - (-7) = 07:00).
        return int(off.total_seconds() // 3600) if off else -8
    except Exception:
        # Sin tzdata: la mitad del año va -7, la otra -8. Ante duda, -8
        # (horario estándar) y lo absorbe la gracia del borde.
        return -7 if 3 <= _dt.datetime.now().month <= 10 else -8


def _process_hour(settings, at=None):
    """Hora de proceso de la liga en UTC.

    `settings.daily_waivers_hour` está en **hora PACÍFICA**, no en UTC ni
    en la del usuario. Verificado contra tres controles independientes:

      Juantasy  campo 0 -> 07:00 UTC = 1 AM CST  ("Clear Waivers:
                Wednesday (1 AM CST)" en el UI de Sleeper)
      Chopped   campo 8 -> 15:00 UTC = 9 AM CST  (el usuario: "9am")
      Dynasty   campo 4 -> 11:00 UTC = 5 AM CST  ("Processing Time
                5 AM CST" en el UI de Sleeper)

    Por eso en 0.1.37, que usaba un 07:00 UTC fijo, daba bien en Juantasy y
    NUCLEAR (ambas valen 0) por casualidad y mal en Chopped y Dynasty.
    """
    try:
        h = int((settings or {}).get("daily_waivers_hour", 0) or 0)
    except (TypeError, ValueError):
        h = 0
    return (h - _pacific_offset(at)) % 24


def _process_days(s):
    """Dias de la semana en que la liga procesa waivers.

    `daily_waivers_days` es una MASCARA DE PASO 2, no de paso 1: cada dia de
    la semana ocupa el bit `dia*2` y los bits impares no los usa ninguna liga.
    Verificado en las 5 ligas de un HAR de la UI de Sleeper (2026-09-30):
    Dynasty 5461 -> bits 0,2,4,6,8,10,12 -> los 7 dias; Chopped 1364 ->
    2,4,6,8,10 -> mar-sab; Juantasy 1092 -> 2,6,10 -> mar/jue/sab. Ni un
    solo bit impar en ninguna. Leerla con paso 1 daba conjuntos plausibles
    pero falsos (Dynasty salia [lun,mie,vie,dom] en vez de los 7 dias), y
    por eso el error no se veía a simple vista.

    Sin diarias (`daily_waivers == 0`) procesa el dia de "Clear Waivers",
    que es `waiver_day_of_week`.
    """
    raw = (s or {}).get("daily_waivers_days")
    try:
        mask = int(raw) if raw is not None and raw != "" else 0
    except (TypeError, ValueError):
        mask = 0
    days = {b // 2 for b in range(14) if mask & (1 << b)}
    if not days:
        # Sin mascara legible se usa el dia de Clear Waivers (0=lunes).
        dow = (s or {}).get("waiver_day_of_week")
        try:
            days = {int(dow)} if dow is not None and dow != "" else {0}
        except (TypeError, ValueError):
            days = {0}
    return {d % 7 for d in days}


def waiver_window(settings: dict, league_id=None, now_ms: int = None,
                  newest_waiver_ms: int = None, live_clears: int = 0):
    """¿El pool de free agents de la liga esta bloqueado (W) o libre (+)?

    La regla de Sleeper es una sola y es la de su propia UI: el pool esta
    bloqueado mientras haya waivers VIVOS, y libre cuando no hay ninguno.
    "W Wed" no es un calendario, es "hay gente en waivers". Eso se lee de
    dos cosas, y las dos estan en los datos:

    1. `waiver_clears_at` por jugador (el "Waiver Time": "Players stay on
       the waivers for 1 day"). Solo aplica DESPUES de un drop; para el
       resto son historia. Viene en SEGUNDOS y `_to_ms` lo pasa a ms.
       `live_clears` es cuantos hay con el valor en el futuro. Dynasty
       tenia 5 al capturar el HAR del 2026-09-30, y por eso salia W.

    2. `status_updated` del ultimo lote de waivers. Sleeper encola a la
       hora exacta pero tarda unos minutos en resolver porque procesa
       liga por liga: medido en vivo, lote encolado entre 04:40 y 07:32
       UTC y TODAS sus transacciones con el MISMO `status_updated`,
       11:04:56, contra las 11:00 en punto del calendario. Mientras now
       sea menor, el lote sigue en la cola.

    Los settings de la liga NO deciden si el pool esta libre; deciden
    CUANDO resuelve cada drop, y son tres cosas distintas que se
    confundian:

    - "Custom Daily Waivers" -> `daily_waivers_days` (mascara de PASO 2:
      bit `dia*2`) + `daily_waivers_hour` (hora PACIFICA, `_process_hour`).
      Dynasty trae 5461 = los 7 dias, y su UI los lista uno por uno.
    - "Clear Waivers" -> `waiver_day_of_week` = INICIO DE SEMANA. Vale 2
      en las 5 ligas de un HAR porque es el default; solo se usa con
      `daily_waivers` apagado.
    - "Waiver Time" -> `waiver_clear_days` = por jugador, post drop.

    Asi que si no hay waivers vivos y el lote ya resolvio, el pool esta
    libre: NO se inventa una ventana por reloj. Antes se calculaba
    `free_until = last + waiver_clear_days` y salia "+" en ligas que no
    abren free agency nunca, que era el bug.

    `next_run_ms` (proximo proceso segun la mascara) es informativo: sale
    en la respuesta para que la UI pueda decir cuando abre la ventana.

    `live_clears=None` significa "no se pudo medir" (falta el JWT o fallo
    Sleeper) y es DISTINTO de `0`. Con `None` no se afirma que el pool este
    libre: sale `unknown`. Ese matiz importa, porque si no, un JWT faltante
    se traduce en un `+` para todo el pool, que es el error caro.

    Devuelve {locked, unknown, until_ms, day, reason, next_run_ms}.
    Sin settings ni senal alguna devuelve `unknown` en vez de afirmar.
    """
    import datetime as dt
    import time as _t
    s = settings or {}
    now = now_ms if now_ms is not None else int(_t.time() * 1000)

    hay_regla = bool(s) and (s.get("daily_waivers_days") is not None
                            or s.get("waiver_day_of_week") is not None)
    newest = 0
    try:
        newest = int(newest_waiver_ms or 0)
    except (TypeError, ValueError):
        newest = 0
    # `None` = no se pudo medir. `0` = medido, no hay ninguno. La diferencia
    # evita el fail-open: sin JWT, `clears` viene vacio y sin esto diria
    # "+" para todo el pool.
    clears_medidos = live_clears is not None
    try:
        vivos = int(live_clears) if clears_medidos else 0
    except (TypeError, ValueError):
        clears_medidos = False
        vivos = 0

    # (1) Lote encolado y todavia sin resolver. Se lee el evento, no se
    # calcula con una constante: por eso no hay WAIVER_BORDER_H ni gracia
    # adivinada.
    if newest > now:
        return {"locked": True, "unknown": False, "until_ms": newest,
                "day": None, "next_run_ms": 0,
                "reason": "Sleeper todavia no termina de procesar waivers"}

    # (2) HAY WAIVERS VIVOS: el pool va con W, como en la UI de Sleeper.
    if vivos > 0:
        return {"locked": True, "unknown": False, "until_ms": 0,
                "day": None, "next_run_ms": 0,
                "reason": ("%d jugador(es) en waivers" % vivos) if vivos > 1
                else "1 jugador en waivers"}

    # (3) Si no hay senal de clears, no se afirma nada.
    if not clears_medidos or not hay_regla:
        return {"locked": False, "unknown": True, "until_ms": 0, "day": None,
                "next_run_ms": 0,
                "reason": "no se pudo leer la regla de waivers de la liga"
                if not hay_regla else
                "no se pudieron leer los waivers en curso"}

    try:
        dow = int(s.get("waiver_day_of_week") or 0)
    except (TypeError, ValueError):
        dow = 0
    hour = _process_hour(s, at=dt.datetime.fromtimestamp(
        now / 1000, dt.timezone.utc))

    tz = _waiver_tz()
    now_dt = dt.datetime.fromtimestamp(now / 1000, tz)
    today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    def _ms(d):
        return int(d.timestamp() * 1000)

    days = _process_days(s)
    nxt = None
    for fwd in range(0, 15):
        d = today + dt.timedelta(days=fwd)
        if d.weekday() in days:
            m = d.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
            if m > now_dt:
                nxt = m
                break

    # (4) DIA DE PROCESO Y TODAVIA NO RESUELTO: la liga esta en su ventana
    # aunque no haya ni un jugador con `clears_at` vivo. Este es el caso de
    # Chopped: corre mar-sab a las 15:00 UTC, y a las 14:45 del miercoles el
    # lote de HOY todavia no existe, asi que el pool va con W. Sin este
    # gate la app le offeria "+" (0.1.39 lo llevo asi).
    #
    # La pregunta NO es "¿que dia es?", sino "¿corrio ya el proceso de HOY?",
    # que es el bug de Juantasy. Se contesta con el dato observado: si el
    # lote mas reciente es ANTERIOR al proceso de hoy, hoy sigue sin
    # resolver. Sin gracia inventada: si el lote de hoy ya esta, la ventana
    # esta abierta.
    hoy_proceso = None
    if now_dt.weekday() in days:
        hoy_proceso = today.replace(hour=hour % 24, minute=0, second=0,
                                    microsecond=0)
    if hoy_proceso is not None and (newest < _ms(hoy_proceso)):
        return {"locked": True, "unknown": False,
                "until_ms": _ms(hoy_proceso), "day": dow,
                "next_run_ms": _ms(hoy_proceso),
                "reason": "la liga procesa hoy y todavia no termino"}

    # (5) Ventana abierta: ni waivers vivos, ni lote pendiente, ni dia de
    # proceso sin resolver.
    return {"locked": False, "unknown": False, "until_ms": 0, "day": None,
            "next_run_ms": _ms(nxt) if nxt else 0,
            "reason": ""}


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
# solo  se usa al resolver (ganador de un lost) — 1 call/liga/día.
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
    # Cache corrupta (escritura a medias, proceso muerto en el `json.dump`)
    # NO puede voltear la app: se trata como cache miss y se re-fetch.
    # Antes el `json.load` saltaba sin try y un archivo a medias dejaba
    # /free-agents y el buscador caidos para siempre, en todas las ligas.
    if os.path.exists(cache_path):
        cached = None
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
        except (OSError, ValueError):
            cached = None
        # Cache vieja sin rank+inj -> refetch una vez y listo.
        sample = next(iter((cached or {}).values()), {})
        if cached and "rank" in sample and "inj" in sample:
            _MAP_CACHE[cache_path] = (_mtime(cache_path), cached)
            return cached
        # Refresh falló antes y nos queda una version previa utilizable: se
        # sirve con el nombre/rank viejos en vez de tumbar la pantalla.
        if cached:
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
    # Escritura ATOMICA: dump a .tmp y os.replace. Escribir en el sitio
    # deja el archivo truncado si el proceso muere a mitad, que es como
    # se fabricaba el cache corrupto de arriba.
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(slim, f)
    os.replace(tmp, cache_path)
    _MAP_CACHE[cache_path] = (_mtime(cache_path), slim)
    return slim


def search_players(query, limit=10, cache_path="players_nfl.json"):
    q = query.lower()
    m = get_players_map(cache_path)
    hits = [(pid, v) for pid, v in m.items() if q in v["name"].lower()]
    return hits[:limit]

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


# La hora publicada es el inicio de la cola, no su resolución. Sleeper tarda
# un tiempo variable en vaciarla (observado: minutos; el usuario reportó
# hasta 15). Este tope genérico solo marca la espera como "pendiente"; no es
# un campo de Sleeper ni una regla por liga.
WAIVER_SETTLE_MINUTES = 15


def normalize_transaction_evidence(transactions):
    """Normaliza la evidencia de transacciones sin inventar ausencias."""
    datos = transactions or {}
    pendientes = set()
    for pid in datos.get("pending_player_ids") or []:
        texto = str(pid)
        if texto and texto != "0":
            pendientes.add(texto)
    try:
        resuelto = int(datos.get("resolved_ms") or 0)
    except (TypeError, ValueError):
        resuelto = 0
    return {
        "ok": datos.get("ok") is True,
        "stale": datos.get("stale") is True,
        "pending_player_ids": pendientes,
        "pending_count": int(datos.get("pending_count") or 0),
        "resolved_ms": max(0, resuelto),
    }


# La cola de la liga vive en sleeper_private.league_waiver_pool_cached. Este
# módulo conserva el nombre histórico para los writers: lo único que hace es
# reventar ese cache compartido.
def bust_waiver_cache(league_id=None):
    """Reventa la evidencia de la cola (writers: send/cancel/update/reorder)."""
    from . import sleeper_private as priv

    priv.bust_pool_waiver_cache(league_id)


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


def _schedule_int(value, default=None):
    """Entero de settings sin comerse ceros válidos ni inventar ausencias."""
    if value is None or value == "" or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def decode_daily_mask(mask):
    """Máscara diaria de Sleeper: bit `2*d` para el día `d` (lunes=0).

    Devuelve `(días, error)`. Solo acepta bits pares dentro de 0..12 porque
    es lo observado; cualquier bit impar o fuera de rango es evidencia
    corrupta, no otro día silencioso.
    """
    if mask is None or mask == "":
        return set(), "sin mascara"
    try:
        numero = int(mask)
    except (TypeError, ValueError):
        return set(), "mascara ilegible"
    if numero <= 0:
        return set(), "mascara vacia"
    dias = set()
    for bit in range(32):
        if numero & (1 << bit):
            if bit % 2 or bit > 12:
                return set(), f"bit inesperado: {bit}"
            dias.add(bit // 2)
    return dias, ""


def build_waiver_schedule(settings, now_ms=None):
    """Calendario puro de waivers: días designados y horas de publicación.

    Esta función NO decide si el pool está libre. Solo traduce las tres
    etiquetas de la UI:
    - `daily_waivers_days` + `daily_waivers_hour`: días y hora de proceso.
    - `waiver_day_of_week`: semana/ciclo semanal, solo si no hay diarias.
    - `waiver_clear_days`: intervalo post-drop por jugador; aquí solo se
      conserva para texto, jamás como ventana del pool.
    """
    import datetime as dt
    import time as _t

    s = settings or {}
    ahora_ms = now_ms if now_ms is not None else int(_t.time() * 1000)
    ahora = dt.datetime.fromtimestamp(ahora_ms / 1000, dt.timezone.utc)
    es_diaria = _schedule_int(s.get("daily_waivers"), 0) == 1
    hora_raw = s.get("daily_waivers_hour")
    hora = _schedule_int(hora_raw)
    semanal = _schedule_int(s.get("waiver_day_of_week"))
    errores = []
    if hora is None:
        errores.append("sin hora de proceso")
    if es_diaria:
        dias, error = decode_daily_mask(s.get("daily_waivers_days"))
        if error:
            errores.append(error)
    else:
        if semanal is None or not 0 <= semanal <= 6:
            errores.append("sin dia semanal")
            dias = set()
        else:
            dias = {semanal % 7}
    valido = not errores
    try:
        from zoneinfo import ZoneInfo
        calendario = ZoneInfo("America/Los_Angeles")
    except Exception:
        calendario = dt.timezone.utc
    # Las etiquetas de días de la UI van con la fecha del Pacífico porque la
    # hora de publicación también es pared del Pacífico.
    hoy_fecha = ahora.astimezone(calendario).date()
    hoy = hoy_fecha.weekday()
    dia_programado = hoy in dias if valido else False
    libres = sorted(set(range(7)) - dias) if valido and es_diaria else []

    def corrida(fecha):
        # La hora siempre es pared del Pacífico en ESA fecha.
        ingenua = dt.datetime(fecha.year, fecha.month, fecha.day, hora % 24)
        try:
            from zoneinfo import ZoneInfo
            return ingenua.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        except Exception:
            ingenua = ingenua.replace(tzinfo=dt.timezone.utc)
        return ingenua

    anterior = actual = siguiente = None
    if valido:
        for atraso in range(0, 22):
            fecha = hoy_fecha - dt.timedelta(days=atraso)
            if fecha.weekday() in dias:
                candidata = corrida(fecha)
                if candidata <= ahora:
                    anterior = candidata
                    break
        if hoy_fecha.weekday() in dias:
            actual = corrida(hoy_fecha)
        for adelanto in range(0, 22):
            fecha = hoy_fecha + dt.timedelta(days=adelanto)
            if fecha.weekday() in dias:
                candidata = corrida(fecha)
                if candidata > ahora:
                    siguiente = candidata
                    break

    def _ms(momento):
        return int(momento.timestamp() * 1000) if momento else 0

    corrida_referencia = actual or siguiente or anterior
    hora_utc = (corrida_referencia.astimezone(dt.timezone.utc).hour
                if corrida_referencia is not None else None)

    return {
        "valid": valido,
        "mode": "daily" if es_diaria else "weekly",
        "process_days": sorted(dias),
        "free_days": libres,
        "schedule_day": dia_programado,
        "weekday": hoy,
        "hour_utc": hora_utc,
        "previous_run_ms": _ms(anterior),
        "current_run_ms": _ms(actual),
        "next_run_ms": _ms(siguiente),
        "weekly_day": semanal,
        "clear_days": _schedule_int(s.get("waiver_clear_days")),
        "errors": errores,
    }


DIAS_WAIVER = ["lunes", "martes", "miércoles", "jueves", "viernes",
                 "sábado", "domingo"]


def waiver_schedule_label(schedule):
    """Texto del ritmo con los días decodificados, sin inventar fechas."""
    programa = schedule or {}
    if not programa.get("valid"):
        return "ritmo de waivers no disponible"
    hora = programa.get("hour_utc")
    hora_txt = f"a las {int(hora):02d}:00 UTC" if hora is not None else ""
    dias = [DIAS_WAIVER[d] for d in programa.get("process_days") or []]
    if programa.get("mode") == "daily":
        if len(dias) == 7:
            return f"corre proceso todos los días {hora_txt}".strip()
        detalle = ", ".join(dias)
        return f"corre proceso {detalle} {hora_txt}".strip()
    if dias:
        return f"corre proceso el {dias[0]} {hora_txt}".strip()
    return "ritmo semanal no disponible"


def classify_waiver_pool(schedule, transactions, clears_ok=True, now_ms=None):
    """Decide el estado del pool con la etiqueta de día y la cola observada.

    La etiqueta diaria de la UI manda sobre el día completo: un día marcado
    como Waivers no se vuelve libre porque no haya filas pendientes. La hora
    publicada solo marca la cola; la resolución observada marca su cierre.
    Un `clears_at` futuro decide la fila correspondiente, jamás el pool.
    """
    import datetime as dt
    import time as _t

    programa = schedule or {}
    evidencia = normalize_transaction_evidence(transactions)
    ahora = now_ms if now_ms is not None else int(_t.time() * 1000)
    clears_medidos = clears_ok is True
    transacciones_ok = evidencia["ok"]
    pendientes = evidencia["pending_player_ids"]
    resuelto = evidencia["resolved_ms"]

    def base(estado, accion, *, bloqueado=False, desconocido=False,
             hasta=0, dia=None, motivo="", corrida=0, esperado=0,
             observado=0):
        return {
            "pool_state": estado,
            "pool_action": accion,
            "locked": bloqueado,
            "unknown": desconocido,
            "until_ms": hasta,
            "day": dia,
            "reason": motivo,
            "next_run_ms": programa.get("next_run_ms", 0),
            "scheduled_run_ms": corrida,
            "expected_resolution_ms": esperado,
            "observed_resolution_ms": observado,
            "pending_league_waivers": len(pendientes),
            "debug": {
                "mode": programa.get("mode"),
                "process_days": list(programa.get("process_days") or []),
                "free_days": list(programa.get("free_days") or []),
                "transactions_ok": transacciones_ok,
                "transactions_stale": evidencia["stale"],
                "clears_measured": clears_medidos,
                "weekly_day": programa.get("weekly_day"),
            },
        }

    def desconocido(motivo):
        return base("unknown", "none", desconocido=True, motivo=motivo)

    if not programa.get("valid"):
        return desconocido("no se pudo leer el calendario de waivers")

    modo = programa.get("mode")
    dias = set(programa.get("process_days") or [])
    hoy = int(programa.get("weekday", -1))
    dia_programado = bool(programa.get("schedule_day"))
    corrida = int(programa.get("current_run_ms") or 0)
    siguiente = int(programa.get("next_run_ms") or 0)
    tope = corrida + WAIVER_SETTLE_MINUTES * 60_000 if corrida else 0
    resuelta = (bool(corrida) and resuelto >= corrida
                and resuelto <= ahora)

    # Semanal sin diarias: lun-mar Pacifico es limbo entre semanas (la nueva
    # abre el miercoles 00 PST): Sleeper no da free agency ahi aunque no sea
    # el dia del ancla. Sin esto, el martes salia "+" donde Sleeper muestra
    # "W". Solo cuando hoy NO es el ancla: el dia del ancla usa su propia
    # logica (antes de publicar bloquea, despues de resolver abre).
    # Limite conocido (lunes de dia): el lunes de dia Sleeper aun da "+" en
    # algunas filas (bye weeks y equipos fuera del Monday Night). Sin un mapa
    # de calendario por equipo no se puede afinar por fila, asi que el pool
    # bloquea de mas: muestra W donde Sleeper daria + en esas filas. Es la
    # direccion segura (un claim de mas, nunca un add indebido) y el lunes
    # casi no hay trafico.
    if modo == "weekly" and hoy in (0, 1) and not dia_programado:
        return base("locked", "claim", bloqueado=True,
                    hasta=siguiente or corrida or 0, dia=hoy,
                    motivo="entre semanas: abre con el proximo cierre",
                    corrida=corrida, esperado=tope, observado=resuelto)

    # Sin evidencia de transacciones, solo se puede afirmar el W que ya dice
    # la propia etiqueta diaria. Nunca se puede afirmar un +.
    if not transacciones_ok:
        if modo == "daily" and dia_programado:
            dia = dt.datetime.fromtimestamp(corrida / 1000,
                                            dt.timezone.utc).weekday() if corrida else hoy
            return base("locked", "claim", bloqueado=True,
                        hasta=siguiente or corrida, dia=dia,
                        motivo="dia de waivers; sin lectura de la cola",
                        corrida=corrida, esperado=tope, observado=resuelto)
        return desconocido("no se pudo leer la cola de waivers")

    if modo == "daily":
        if dia_programado:
            if ahora < corrida:
                dia = dt.datetime.fromtimestamp(
                    corrida / 1000, dt.timezone.utc).weekday()
                return base("locked", "claim", bloqueado=True, hasta=corrida,
                            dia=dia,
                            motivo="la publicacion de hoy todavia no llega",
                            corrida=corrida, esperado=tope, observado=resuelto)
            if not resuelta and ahora <= tope:
                return base(
                    "uncertain", "none", desconocido=True, hasta=tope,
                    dia=hoy,
                    motivo="publicado y sin resolucion observada",
                    corrida=corrida, esperado=tope, observado=resuelto)
            siguiente_dia = (dt.datetime.fromtimestamp(
                siguiente / 1000, dt.timezone.utc).weekday()
                if siguiente else hoy)
            return base("locked", "claim", bloqueado=True, hasta=siguiente,
                        dia=siguiente_dia,
                        motivo="dia marcado como Waivers en la liga",
                        corrida=corrida, esperado=tope, observado=resuelto)
        if not clears_medidos:
            return desconocido("no se pudieron leer los waivers en curso")
        return base("free", "pickup",
                    motivo="dia marcado como Free Agents en la liga",
                    corrida=corrida, esperado=tope, observado=resuelto)

    # Semanal: el ancla es el único día con publicación programada.
    if dia_programado:
        if not corrida:
            return desconocido("semana sin corrida programada")
        dia = dt.datetime.fromtimestamp(corrida / 1000,
                                        dt.timezone.utc).weekday()
        if ahora < corrida:
            return base("locked", "claim", bloqueado=True, hasta=corrida,
                        dia=dia, motivo="el cierre semanal todavia no llega",
                        corrida=corrida, esperado=tope, observado=resuelto)
        if not resuelta and ahora <= tope:
            return base("uncertain", "none", desconocido=True, hasta=tope,
                        dia=dia,
                        motivo="publicado y sin resolucion observada",
                        corrida=corrida, esperado=tope, observado=resuelto)
        if resuelta or ahora > tope:
            if not clears_medidos:
                return desconocido("no se pudieron leer los waivers en curso")
            return base("free", "pickup",
                        motivo="cierre semanal observado o sin cola activa",
                        corrida=corrida, esperado=tope, observado=resuelto)
        return desconocido("semana sin evidencia suficiente")

    if not clears_medidos:
        return desconocido("no se pudieron leer los waivers en curso")
    return base("free", "pickup", motivo="fuera del dia de cierre semanal",
                corrida=corrida, esperado=tope, observado=resuelto)


def resolve_player_row(pid, clears, pending_player_ids, pool, now_ms=None):
    """Estado efectivo de UNA fila: el pool nunca hereda su `clears_at`."""
    import time as _t

    ahora = now_ms if now_ms is not None else int(_t.time() * 1000)
    pid = str(pid)
    propio = (clears or {}).get(pid)
    futuro = isinstance(propio, (int, float)) and not isinstance(propio, bool) and propio > ahora
    pendiente = pid in set(pending_player_ids or [])
    pool = pool or {}
    estado_pool = pool.get("pool_state")

    if futuro or pendiente:
        estado, accion, bloqueada, desconocida = "locked", "claim", True, False
        # El bloqueo efectivo dura hasta que pasen TODAS las restricciones:
        # la fecha propia y, si existe, la ventana del pool.
        hasta = propio if futuro else None
        pool_hasta = pool.get("until_ms") or None
        if pool_hasta:
            hasta = pool_hasta if hasta is None else max(hasta, pool_hasta)
        detalle = ("transacción pendiente" if pendiente and not futuro
                   else "jugador en waivers por drop reciente")
    elif estado_pool == "uncertain":
        estado, accion, bloqueada, desconocida = "uncertain", "none", False, True
        hasta = pool.get("until_ms")
        detalle = pool.get("reason", "")
    elif estado_pool == "unknown":
        estado, accion, bloqueada, desconocida = "unknown", "none", False, True
        hasta = None
        detalle = pool.get("reason", "")
    elif estado_pool == "locked":
        estado, accion, bloqueada, desconocida = "locked", "claim", True, False
        hasta = pool.get("until_ms")
        detalle = pool.get("reason", "")
    elif estado_pool == "free":
        estado, accion, bloqueada, desconocida = "free", "pickup", False, False
        hasta = None
        detalle = "agente libre"
    else:
        estado, accion, bloqueada, desconocida = "unknown", "none", False, True
        hasta = None
        detalle = pool.get("reason", "") or "estado del pool no reconocido"
    return {"player_id": pid, "clears_at": propio if futuro else None,
            "pending_transaction": pendiente, "on_waivers": bloqueada,
            "waiver_state": estado, "waiver_action": accion,
            "waiver_detail": detalle, "pool_state": estado_pool,
            "resolves_at": hasta,
            "resolves_day": (pool.get("day")
                             if estado_pool == "locked" and not futuro else None),
            "waiver_unknown": desconocida}


def waiver_window(settings, now_ms=None, transactions=None, clears_ok=None):
    """Ventana del pool con la etiqueta del día y la cola observada.

    - La UI marca los días: Waivers o Free Agents.
    - `waiver_clears_at` decide filas individuales y nunca se agrega al pool.
    - La cola privada decide transacciones pendientes/resueltas.
    - La hora publicada abre la espera; la resolución observada la cierra.
    """
    programa = build_waiver_schedule(settings, now_ms=now_ms)
    return classify_waiver_pool(programa, transactions, clears_ok, now_ms)


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

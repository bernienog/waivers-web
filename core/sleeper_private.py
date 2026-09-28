"""Capa privada: UNICA parte que conoce el endpoint GraphQL de Sleeper.

MVP FAAB, expandible a priority: settings es dict generico.
  FAAB:     {"waiver_bid": 33}
  Priority: {} (sin bid, solo orden de claims)

Auth: header `authorization: <JWT>` + cookie `sleeper-web-session=<JWT>`.
Nunca loguear valores completos, solo longitudes.
"""

import os

from .fetch import sleeper_post

GRAPHQL_URL = "https://sleeper.com/graphql"

SUBMIT_FAAB_QUERY_TMPL = (
    "mutation submit_waiver_claim($k_adds: [String], $v_adds: [Int],"
    " $k_drops: [String], $v_drops: [Int],"
    " $k_settings: [String], $v_settings: [Int]) {{\n"
    '  submit_waiver_claim(league_id: "{league_id}",'
    "k_adds: $k_adds,v_adds: $v_adds,k_drops: $k_drops,"
    "v_drops: $v_drops,k_settings: $k_settings,v_settings: $v_settings){{\n"
    "    adds consenter_ids created creator drops league_id leg metadata"
    " roster_ids settings status status_updated transaction_id type player_map\n"
    "  }}\n"
    "}}"
)

# Claim normal (waiver_type 0/1): sin k_settings/v_settings (captura Nuclear)
SUBMIT_CLAIM_QUERY_TMPL = (
    "mutation submit_waiver_claim($k_adds: [String], $v_adds: [Int],"
    " $k_drops: [String], $v_drops: [Int]) {{\n"
    '  submit_waiver_claim(league_id: "{league_id}",'
    "k_adds: $k_adds,v_adds: $v_adds,k_drops: $k_drops,"
    "v_drops: $v_drops){{\n"
    "    adds consenter_ids created creator drops league_id leg metadata"
    " roster_ids settings status status_updated transaction_id type player_map\n"
    "  }}\n"
    "}}"
)

CANCEL_QUERY_TMPL = (
    "mutation cancel_waiver_claim {{\n"
    '  cancel_waiver_claim(league_id: "{league_id}",leg: {leg},'
    'transaction_id: "{transaction_id}"){{\n'
    "    adds consenter_ids created creator drops league_id leg metadata"
    " roster_ids settings status status_updated transaction_id type player_map\n"
    "  }}\n"
    "}}"
)

# Update in-place (mismo transaction_id, NO duplica). Capturas:
# FAAB bid: k_settings=["waiver_bid"], v_settings=[91]
# Claim priority: k_settings=["priority"], v_settings=[0|1] (una mutación
# por claim, en orden — valida nuestra cola secuencial)
UPDATE_QUERY_TMPL = (
    "mutation update_waiver_claim($k_settings: [String], $v_settings: [Int]) {{\n"
    '  update_waiver_claim(league_id: "{league_id}",'
    'transaction_id: "{transaction_id}",leg: {leg},'
    "k_settings: $k_settings,v_settings: $v_settings){{\n"
    "    adds consenter_ids created creator drops league_id leg metadata"
    " roster_ids settings status status_updated transaction_id type player_map\n"
    "  }}\n"
    "}}"
)


def build_faab_settings(bid):
    return {"waiver_bid": int(bid)}


def _headers(jwt):
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://sleeper.com",
        "Referer": "https://sleeper.com/",
        "x-sleeper-graphql-op": "submit_waiver_claim",
        "Authorization": jwt,
    }


def _cookies(session):
    return {"sleeper-web-session": session} if session else {}


def build_submit_payload(league_id, add_id, drop_id, roster_id, settings):
    """settings: dict, ej {"waiver_bid": 33} o {} para claim normal.
    Sin drop (slot libre): k/v_drops vacíos, el add viaja solo."""
    has_drop = bool(str(drop_id or ""))
    drops_k = [str(drop_id)] if has_drop else []
    drops_v = [int(roster_id)] if has_drop else []
    if settings:
        k_settings = sorted(settings.keys())
        v_settings = [int(settings[k]) for k in k_settings]
        return {
            "operationName": "submit_waiver_claim",
            "query": SUBMIT_FAAB_QUERY_TMPL.format(league_id=league_id),
            "variables": {
                "k_adds": [str(add_id)],
                "v_adds": [int(roster_id)],
                "k_drops": drops_k,
                "v_drops": drops_v,
                "k_settings": k_settings,
                "v_settings": v_settings,
            },
        }
    return {
        "operationName": "submit_waiver_claim",
        "query": SUBMIT_CLAIM_QUERY_TMPL.format(league_id=league_id),
        "variables": {
            "k_adds": [str(add_id)],
            "v_adds": [int(roster_id)],
            "k_drops": drops_k,
            "v_drops": drops_v,
        },
    }


def submit_transaction(league_id, add_id, drop_id, roster_id, settings,
                       jwt=None, session=None, timeout=20):
    """Envia 1 waiver claim. Retorna (ok, data, raw). Valida pending+transaction_id."""
    jwt = jwt or os.environ.get("SLEEPER_JWT", "")
    session = session or os.environ.get("SLEEPER_SESSION", "")
    if not jwt:
        raise ValueError("Falta SLEEPER_JWT en env/.env")
    payload = build_submit_payload(league_id, add_id, drop_id, roster_id, settings)
    r = sleeper_post(GRAPHQL_URL, json=payload,
                     headers=_headers(jwt), cookies=_cookies(session),
                     timeout=timeout)
    raw = r.text[:8000]
    if r.status_code != 200:
        return False, None, raw
    try:
        data = r.json()["data"]["submit_waiver_claim"]
    except Exception:
        return False, None, raw
    ok = (
        data is not None
        and data.get("status") == "pending"
        and data.get("type") == "waiver"
        and data.get("transaction_id")
    )
    return bool(ok), data, raw


def cancel_claim(league_id, transaction_id, leg=1,
                 jwt=None, session=None, timeout=20):
    """Rollback/test: cancela un claim pending."""
    jwt = jwt or os.environ.get("SLEEPER_JWT", "")
    session = session or os.environ.get("SLEEPER_SESSION", "")
    payload = {
        "operationName": "cancel_waiver_claim",
        "variables": {},
        "query": CANCEL_QUERY_TMPL.format(
            league_id=league_id, leg=int(leg), transaction_id=transaction_id),
    }
    headers = _headers(jwt)
    headers["x-sleeper-graphql-op"] = "cancel_waiver_claim"
    r = sleeper_post(GRAPHQL_URL, json=payload,
                     headers=headers, cookies=_cookies(session),
                     timeout=timeout)
    return r.status_code == 200, r.text[:4000]


LIST_TX_QUERY_TMPL = (
    "query league_transactions_filtered {{\n"
    "  league_transactions_filtered(league_id: \"{league_id}\","
    "roster_id_filters: [{roster_filter}],"
    "type_filters: [{type_filters}],leg_filters: [],status_filters: [{status_filters}]){{\n"
    "    adds consenter_ids created creator draft_picks drops league_id leg"
    " metadata roster_ids settings status status_updated transaction_id"
    " type player_map waiver_budget\n"
    "  }}\n"
    "}}"
)


def _list_tx_fetch(league_id, roster_filter, type_filters, status_filters,
                   jwt, session, timeout):
    payload = {
        "operationName": "league_transactions_filtered",
        "variables": {},
        "query": LIST_TX_QUERY_TMPL.format(
            league_id=league_id, roster_filter=roster_filter,
            type_filters=type_filters, status_filters=status_filters),
    }
    headers = _headers(jwt)
    headers["x-sleeper-graphql-op"] = "league_transactions_filtered"
    r = sleeper_post(GRAPHQL_URL, json=payload,
                     headers=headers, cookies=_cookies(session),
                     timeout=timeout)
    r.raise_for_status()
    return r.json()["data"]["league_transactions_filtered"] or []


def list_transactions(league_id, roster_id=None, jwt=None, session=None,
                      timeout=20, narrow=True):
    """Lee transacciones (incluye pending) via GraphQL autenticado.
    Retorna lista de dicts. roster_id=None trae toda la liga.
    narrow=True (default): angosto primero (solo waiver + pending =
    respuesta de filas, no la historia de la temporada); si Sleeper
    rechaza los filtros, fallback al query amplio de antes y se filtra
    en Python. narrow=False: siempre el amplio (diagnóstico de orden)."""
    jwt = jwt or os.environ.get("SLEEPER_JWT", "")
    session = session or os.environ.get("SLEEPER_SESSION", "")
    if not jwt:
        raise ValueError("Falta SLEEPER_JWT en env/.env")
    roster_filter = str(int(roster_id)) if roster_id is not None else ""
    if narrow:
        try:
            out = _list_tx_fetch(league_id, roster_filter,
                                 '"waiver"', '"pending"',
                                 jwt, session, timeout)
            _PENDING_STATS["narrow_ok"] += 1
            return out
        except Exception:
            # El fallback sigue ahí a propósito: si Sleeper está inestable
            # preferimos el query amplio a perder los pendings. Solo se
            # cuenta para poder decidirlo con datos.
            _PENDING_STATS["wide_fallbacks"] += 1
    out = _list_tx_fetch(league_id, roster_filter, "", "",
                         jwt, session, timeout)
    _PENDING_STATS["wide_ok"] += 1
    return out


# Cache corto de pendings: el Hub pedía verify(lid) + pending(lid) celérrimos
# seguidos (mismo dato dos veces) más cada poll. 30s, se revienta en cada
# writer (send/cancel/update/reorder). Cambios hechos en la UI de Sleeper
# pueden tardar hasta el TTL en aparecer — verify sigue siendo autoridad
# para resoluciones.
_PENDING_CACHE: dict = {}  # league_id -> (epoch_s, [tx])
PENDING_TTL = 30

# Telemetría. No cambia el comportamiento: sirve para ver si los atajos que
# NO tocamos (el `fresh=1` del ↻, el fallback narrow->wide) realmente pasan.
# Si dan ~0, el ítem se puede borrar del plan sin riesgo.
_PENDING_STATS = {"fresh_bypasses": 0, "wide_fallbacks": 0,
                  "narrow_ok": 0, "wide_ok": 0, "cache_hits": 0}


def pending_stats():
    return dict(_PENDING_STATS)


def bust_pending(league_id=None):
    if league_id is None:
        _PENDING_CACHE.clear()
    else:
        # Las claves son (lid, scope): reventar TODOS los scopes de la
        # liga. Antes se popeaba el lid plano y el bust jamás acertaba —
        # el pending muerto resucitaba hasta 30s (el "drop fantasma").
        lid = str(league_id)
        for key in [k for k in _PENDING_CACHE if k[0] == lid]:
            _PENDING_CACHE.pop(key, None)


def list_pending_cached(league_id, roster_id=None, jwt=None, session=None,
                        timeout=20, fresh=False):
    import time as _time
    # Clave con scope: el verify usa roster None (toda la liga) y el Hub
    # usa mi roster — jamás mezclarlos.
    key = (str(league_id),
           "" if roster_id is None else str(int(roster_id)))
    if not fresh:
        hit = _PENDING_CACHE.get(key)
        if hit and _time.time() - hit[0] < PENDING_TTL:
            _PENDING_STATS["cache_hits"] += 1
            return hit[1]
    else:
        _PENDING_STATS["fresh_bypasses"] += 1
    rows = [t for t in list_transactions(league_id, roster_id, jwt,
                                         session, timeout)
            if t.get("type") == "waiver" and t.get("status") == "pending"]
    _PENDING_CACHE[key] = (_time.time(), rows)
    return rows


def list_pending(league_id, roster_id=None, jwt=None, session=None,
                   timeout=20):
    """Subconjunto pending tipo waiver. La via 'a priori' fuera del submit."""
    txs = list_transactions(league_id, roster_id, jwt, session, timeout)
    return [t for t in txs
            if t.get("type") == "waiver" and t.get("status") == "pending"]


LEAGUE_PLAYERS_QUERY_TMPL = (
    "query league_players {{\n"
    '  league_players(league_id: "{league_id}"){{\n'
    "    league_id metadata player_id settings\n"
    "  }}\n"
    "}}"
)


def _to_ms(v):
    """waiver_clears_at viene en segundos (10 digitos); otros campos en ms.
    Normaliza a ms por magnitud."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n * 1000 if n < 1_000_000_000_000 else n


def _league_player_rows(league_id, jwt=None, session=None, timeout=20):
    """Filas RAW de league_players (settings+metadata sin parsear)."""
    import os as _os
    jwt = jwt or _os.environ.get("SLEEPER_JWT", "")
    session = session or _os.environ.get("SLEEPER_SESSION", "")
    if not jwt:
        raise ValueError("Falta SLEEPER_JWT en env/.env")
    payload = {
        "operationName": "league_players",
        "variables": {},
        "query": LEAGUE_PLAYERS_QUERY_TMPL.format(league_id=league_id),
    }
    headers = _headers(jwt)
    headers["x-sleeper-graphql-op"] = "league_players"
    r = sleeper_post(GRAPHQL_URL, json=payload,
                     headers=headers, cookies=_cookies(session),
                     timeout=timeout)
    r.raise_for_status()
    return (r.json().get("data") or {}).get("league_players") or []


def get_player_row(league_id, player_id, jwt=None, session=None,
                   timeout=20):
    """Fila RAW de league_players para un player. Diagnóstico del
    discriminador FA/WAIVER. None si ausente (la ausencia también es
    señal)."""
    for row in _league_player_rows(league_id, jwt, session, timeout):
        if str((row or {}).get("player_id")) == str(player_id):
            return row
    return None


def get_waiver_clears(league_id, jwt=None, session=None, timeout=20):
    """Mirror de la UI: settings.waiver_clears_at por player (ms).
    Solo jugadores en waivers traen el campo; resto se omite.
    Retorna {player_id: clears_at_ms}. Misma familia GraphQL + JWT."""
    out = {}
    for row in _league_player_rows(league_id, jwt, session, timeout):
        pid = str((row or {}).get("player_id") or "")
        if not pid or pid == "0":
            continue
        ts = ((row or {}).get("settings") or {}).get("waiver_clears_at")
        ms = _to_ms(ts)
        if ms:
            out[pid] = ms
    return out


FREE_AGENT_TX_QUERY_TMPL = (
    "mutation league_create_transaction($k_adds: [String],"
    " $v_adds: [Int], $k_drops: [String], $v_drops: [Int]) {{\n"
    '  league_create_transaction(league_id: "{league_id}",type: "free_agent",'
    "k_adds: $k_adds,v_adds: $v_adds,k_drops: $k_drops,v_drops: $v_drops){{\n"
    "    adds consenter_ids created creator drops league_id leg metadata"
    " roster_ids settings status status_updated transaction_id type player_map\n"
    "  }}\n"
    "}}"
)


def _free_agent_ok(data):
    return bool(
        data is not None
        and data.get("status") == "complete"
        and data.get("type") == "free_agent"
        and data.get("transaction_id")
    )


def add_free_agent(league_id, add_id, drop_id, roster_id,
                   jwt=None, session=None, timeout=20):
    """Pickup directo de FA libre (sin lock). Wire-validado contra llamada
    real aceptada: k/v_drops vacíos, type free_agent inline.
    OJO: completa AL INSTANTE (status complete, sin pending que cancelar).
    Retorna (ok, data, raw)."""
    jwt = jwt or os.environ.get("SLEEPER_JWT", "")
    session = session or os.environ.get("SLEEPER_SESSION", "")
    if not jwt:
        raise ValueError("Falta SLEEPER_JWT en env/.env")
    has_drop = bool(str(drop_id or ""))
    payload = {
        "operationName": "league_create_transaction",
        "variables": {
            "k_adds": [str(add_id)],
            "v_adds": [int(roster_id)],
            "k_drops": [str(drop_id)] if has_drop else [],
            "v_drops": [int(roster_id)] if has_drop else [],
        },
        "query": FREE_AGENT_TX_QUERY_TMPL.format(league_id=league_id),
    }
    headers = _headers(jwt)
    headers["x-sleeper-graphql-op"] = "league_create_transaction"
    r = sleeper_post(GRAPHQL_URL, json=payload,
                     headers=headers, cookies=_cookies(session),
                     timeout=timeout)
    raw = r.text[:8000]
    if r.status_code != 200:
        return False, None, raw
    try:
        data = r.json()["data"]["league_create_transaction"]
    except Exception:
        return False, None, raw
    return _free_agent_ok(data), data, raw


def update_claim(league_id, transaction_id, settings, leg=1,
                 jwt=None, session=None, timeout=20):
    """Edita un pending in-place. settings: {"waiver_bid": N} o {"priority": N}.
    Retorna (ok, data, raw). Valida que el settings aplicado matchee."""
    jwt = jwt or os.environ.get("SLEEPER_JWT", "")
    session = session or os.environ.get("SLEEPER_SESSION", "")
    if not jwt:
        raise ValueError("Falta SLEEPER_JWT en env/.env")
    if not settings or not transaction_id:
        raise ValueError("settings y transaction_id requeridos")
    k = sorted(settings.keys())
    payload = {
        "operationName": "update_waiver_claim",
        "variables": {"k_settings": k,
                      "v_settings": [int(settings[x]) for x in k]},
        "query": UPDATE_QUERY_TMPL.format(
            league_id=league_id, transaction_id=transaction_id, leg=int(leg)),
    }
    headers = _headers(jwt)
    headers["x-sleeper-graphql-op"] = "update_waiver_claim"
    r = sleeper_post(GRAPHQL_URL, json=payload,
                     headers=headers, cookies=_cookies(session),
                     timeout=timeout)
    raw = r.text[:4000]
    if r.status_code != 200:
        return False, None, raw
    try:
        data = r.json()["data"]["update_waiver_claim"]
    except Exception:
        return False, None, raw
    ok = (
        data is not None
        and data.get("transaction_id") == str(transaction_id)
        and all(int((data.get("settings") or {}).get(x, -1)) == int(settings[x])
                for x in k)
    )
    return bool(ok), data, raw

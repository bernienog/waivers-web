"""Puntos de fantasy desde las ESTADÍSTICAS proyectadas.

Por qué esto existe: el payload público de Sleeper trae `pts_ppr`, pero
NO son puntos por recepción (Mariota week 3: 13.08 = scoring estándar).
La UI de Sleeper multiplica cada stat por el `scoring_settings` de TU
liga — que es PÚBLICO (clave top-level del endpoint de liga, no dentro
de settings). Ejemplo real: NUCLEAR da 2.0 por recepción, 0.05/yd
(20 ydas por punto), 6 por TD de pase, 0.5 por completación y 1.0 por
intento de carrera. Por eso su lista de FAs son QBs.

El motor es genérico: puntos = Σ stat × valor_de_la_liga. No hay
fórmulas por posición: K y DEF caen solos porque sus stats tienen sus
propios valores. Si la liga no trae settings, se usa DEFAULT (PPR+TEP).
"""


def points(stats, settings=None):
    """Suma stat×valor de la liga. Sin settings: DEFAULT (PPR+TEP)."""
    if not stats:
        return None
    sc = settings or DEFAULT
    total = 0.0
    for key, val in stats.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        w = sc.get(key)
        if isinstance(w, (int, float)) and w:
            total += float(val) * float(w)
    return round(total, 2) or None


# Fallback para ligas sin scoring_settings. PPR + TEP, lo más común.
DEFAULT = {
    "rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1, "pass_yd": 0.04,
    "pass_td": 4.0, "rush_td": 6.0, "rec_td": 6.0,
    "pass_int": -2.0, "fum_lost": -2.0, "pass_2pt": 2.0,
    "rec_2pt": 2.0, "rush_2pt": 2.0, "xpm": 3.0, "xpmiss": -1.0,
    "sack": 2.0, "safe": 4.0, "int": 3.0, "tkl_loss": 1.0,
    "blk_kick": 3.0, "st_td": 6.0, "pr_td": 6.0, "pr_yd": 0.1,
    "def_td": 6.0, "def_pr_td": 6.0, "def_pr_yd": 0.1,
    "fgm_0_19": 3.0, "fgm_20_29": 3.0, "fgm_30_39": 3.0,
    "fgm_40_49": 3.0, "fgm_50p": 4.0,
}

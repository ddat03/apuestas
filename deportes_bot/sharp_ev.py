# ============================================================
#  sharp_ev.py — Detección de valor +EV con ancla Pinnacle
#
#  Reemplaza al value_analyzer viejo, que comparaba la mejor
#  cuota contra el PROMEDIO de las mismas casas blandas → eso
#  fabricaba "valor" falso en cuotas altas (sesgo favorito-
#  longshot). Aquí el ancla es Pinnacle (o Betfair Exchange):
#  el mercado más eficiente. Solo hay valor si una casa blanda
#  paga MÁS que la probabilidad justa (sin margen) de Pinnacle.
#
#  No usa ML. No predice resultados. Solo compara precios.
# ============================================================

from dataclasses import dataclass

# Casa de referencia: la línea "sharp" contra la que se mide el valor.
ANCLA = "pinnacle"

# Exchanges: se puede apostar contra ellos, pero cobran comisión
# sobre la ganancia neta — se descuenta del EV.
EXCHANGES = {"betfair_ex_eu", "betfair_ex_uk", "matchbook", "smarkets"}
COMISION_EXCHANGE = 0.02

# Casas que nunca cuentan como "blanda" donde colocar la apuesta
NO_APOSTAR_EN = {ANCLA}

RESULTADOS = ("local", "empate", "visitante")


@dataclass
class Pick:
    sport: str
    event_id: str
    home: str
    away: str
    commence_time: str
    outcome: str            # local | empate | visitante
    fair_prob: float        # prob. justa sin margen según el ancla
    ancla_odds: float       # cuota del ancla para ese outcome (con margen)
    best_odds: float        # mejor cuota disponible en casa blanda
    best_book: str
    ev: float               # valor esperado neto (ya con comisión si aplica)
    n_books: int


def _novig(odds: dict[str, float]) -> dict[str, float]:
    """Probabilidades justas (suman 1) quitando el margen de la casa."""
    inv = {k: 1.0 / v for k, v in odds.items() if v and v > 1.0}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s else {}


def _clasificar_outcome(nombre: str, home: str, away: str) -> str | None:
    n = nombre.strip().lower()
    if n == "draw":
        return "empate"
    if n in home.lower() or home.lower() in n or n[:6] == home.lower()[:6]:
        return "local"
    if n in away.lower() or away.lower() in n or n[:6] == away.lower()[:6]:
        return "visitante"
    return None


def _precios_por_casa(event: dict) -> dict[str, dict[str, float]]:
    """{casa: {local: o, empate: o, visitante: o}} a partir del h2h."""
    home, away = event["home_team"], event["away_team"]
    out: dict[str, dict[str, float]] = {}
    for bk in event.get("bookmakers", []):
        precios: dict[str, float] = {}
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for o in mkt.get("outcomes", []):
                clave = _clasificar_outcome(o.get("name", ""), home, away)
                precio = float(o.get("price", 0) or 0)
                if clave and precio > 1.0:
                    precios[clave] = precio
        if len(precios) == 3:
            out[bk["key"]] = precios
    return out


def mejor_precio(por_casa: dict[str, dict[str, float]], outcome: str) -> tuple[float, str]:
    """La mejor cuota disponible para `outcome` entre las casas donde
    SÍ se puede apostar (excluye el ancla, que solo sirve de referencia)."""
    mejor_odds, mejor_book = 0.0, ""
    for casa, precios in por_casa.items():
        if casa in NO_APOSTAR_EN:
            continue
        p = precios.get(outcome, 0.0)
        if p > mejor_odds:
            mejor_odds, mejor_book = p, casa
    return mejor_odds, mejor_book


def analizar_evento(event: dict, ev_min: float = 0.02, ev_max: float = 0.15,
                    min_books: int = 6, ancla: str = ANCLA) -> list[Pick]:
    """Devuelve los Pick con valor de UN evento. Lista vacía si no hay
    ancla, si hay pocas casas, o si ningún outcome supera el umbral."""
    por_casa = _precios_por_casa(event)
    if ancla not in por_casa or len(por_casa) < min_books:
        return []

    fair = _novig(por_casa[ancla])
    if len(fair) != 3:
        return []

    home, away = event["home_team"], event["away_team"]
    picks: list[Pick] = []

    for outcome in RESULTADOS:
        mejor_odds, mejor_book = mejor_precio(por_casa, outcome)
        if mejor_odds <= 1.0:
            continue

        p_justa = fair[outcome]
        # EV con la cuota nominal; si es exchange, se descuenta comisión
        # sobre la ganancia neta (la parte que supera a 1.0)
        if mejor_book in EXCHANGES:
            odds_efectiva = 1.0 + (mejor_odds - 1.0) * (1.0 - COMISION_EXCHANGE)
        else:
            odds_efectiva = mejor_odds
        ev = p_justa * odds_efectiva - 1.0

        # Banda de cordura: un EV altísimo casi siempre es cuota stale
        # o mal emparejada, no valor real.
        if ev_min <= ev <= ev_max:
            picks.append(Pick(
                sport=event.get("sport_key", ""),
                event_id=event.get("id", ""),
                home=home, away=away,
                commence_time=event.get("commence_time", ""),
                outcome=outcome,
                fair_prob=round(p_justa, 4),
                ancla_odds=round(por_casa[ancla][outcome], 3),
                best_odds=round(mejor_odds, 3),
                best_book=mejor_book,
                ev=round(ev, 4),
                n_books=len(por_casa),
            ))
    return picks


def ev_vs_cierre(odds_tomada: float, fair_prob_cierre: float) -> float:
    """CLV en forma de EV: usando la probabilidad justa de la línea de
    CIERRE del ancla como 'verdad', ¿la cuota que tomamos tenía valor?
    Positivo y consistente = el sistema tiene edge real."""
    return round(fair_prob_cierre * odds_tomada - 1.0, 4)

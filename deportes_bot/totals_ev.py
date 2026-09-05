# ============================================================
#  totals_ev.py — Mercado de goles (Más/Menos) con ancla Pinnacle
#
#  Creado por Diego Aleman.
#
#  A diferencia de sharp_ev.py (1X2), acá comparar contra la línea
#  PRINCIPAL de Pinnacle casi nunca sirve para encontrar una pata
#  "segura": un libro sharp pone su línea principal de goles
#  EXACTAMENTE donde el mercado está parejo (~50/50) — es su trabajo.
#  Verificado en vivo: en 19 partidos reales de la Premier League,
#  ninguna línea principal de Pinnacle superó el 55% de probabilidad
#  para ningún lado.
#
#  Para encontrar probabilidades realmente sesgadas (Over 1.5 al
#  85%, Under 4.5 al 92%, etc.) hay que mirar líneas ALTERNATIVAS —
#  que Pinnacle no siempre cotiza, pero casas blandas sí. Se resuelve
#  calibrando un modelo Poisson de goles totales con la línea
#  principal de Pinnacle (técnica estándar: de la cuota justa a
#  X.5 se puede despejar el λ esperado de goles), y con ese λ se
#  puede leer la probabilidad justa en CUALQUIER línea, la cotice
#  Pinnacle o no. La fuente de la probabilidad sigue siendo 100%
#  el precio de Pinnacle — el Poisson es solo la forma de
#  interpolar/extrapolar entre líneas, no un modelo estadístico
#  aparte inventando números.
#
#  Real, no adivinado: esto reemplaza a la estimación de goles que
#  antes hacía ai_analyzer.py pidiéndole a GPT que "estimara" con
#  su propio criterio.
# ============================================================

import math
from dataclasses import dataclass

import sharp_ev

_RESULTADOS = ("Over", "Under")


def _precios_totals_por_casa(event: dict) -> dict[str, dict[float, dict[str, float]]]:
    """{casa: {línea: {"Over": odds, "Under": odds}}} a partir del mercado totals.
    Solo se conservan líneas donde la casa cotiza AMBOS lados."""
    out: dict[str, dict[float, dict[str, float]]] = {}
    for bk in event.get("bookmakers", []):
        lineas: dict[float, dict[str, float]] = {}
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "totals":
                continue
            for o in mkt.get("outcomes", []):
                punto  = o.get("point")
                nombre = o.get("name")
                precio = float(o.get("price", 0) or 0)
                if punto is None or nombre not in _RESULTADOS or precio <= 1.0:
                    continue
                lineas.setdefault(punto, {})[nombre] = precio
        lineas = {p: v for p, v in lineas.items() if len(v) == 2}
        if lineas:
            out[bk["key"]] = lineas
    return out


# ── Modelo Poisson para interpolar/extrapolar entre líneas ────────────

def _poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) para X ~ Poisson(lam), sin depender de scipy."""
    if lam <= 0:
        return 1.0
    termino = math.exp(-lam)
    suma = termino
    for i in range(1, k + 1):
        termino *= lam / i
        suma += termino
    return min(suma, 1.0)


def _lambda_desde_linea(linea: float, prob_over: float) -> float | None:
    """Encuentra λ (goles totales esperados) tal que P(Over linea) = prob_over
    bajo goles ~ Poisson(λ). P(Over) crece monótono con λ → bisección."""
    k = math.floor(linea)
    lo, hi = 1e-6, 15.0
    if _poisson_cdf(k, hi) > 1.0 - prob_over:
        return None  # prob demasiado alta para el rango razonable de goles
    for _ in range(60):
        mid = (lo + hi) / 2
        p_over = 1.0 - _poisson_cdf(k, mid)
        if p_over < prob_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _prob_en_linea(lam: float, linea: float, lado: str) -> float:
    k = math.floor(linea)
    p_over = 1.0 - _poisson_cdf(k, lam)
    return p_over if lado == "Over" else 1.0 - p_over


@dataclass
class PataTotals:
    sport: str
    event_id: str
    home: str
    away: str
    commence_time: str
    outcome: str            # "over_1.5" | "under_4.5" — mismo campo TEXT de siempre en clv_db
    fair_prob: float
    ancla_odds: float        # cuota IMPLÍCITA del modelo Poisson calibrado a Pinnacle (no siempre cotizada literalmente por Pinnacle)
    best_odds: float
    best_book: str
    ev: float
    n_books: int


def pata_segura_evento(event: dict, prob_min: float = 0.70,
                       min_books: int = 6, max_descuento: float = 0.08,
                       ev_max: float = 0.15,
                       ancla: str = sharp_ev.ANCLA) -> PataTotals | None:
    """La pata más segura del mercado de goles: calibra λ con la línea
    principal de Pinnacle y evalúa CADA línea que alguna casa blanda
    ofrezca (no solo la de Pinnacle) con la probabilidad que ese mismo
    λ implica. None si no hay ancla, faltan casas, no se puede calibrar
    λ, o ninguna línea alcanza el umbral sin pagar de más por ella.

    ev_max: techo de cordura. El Poisson es una aproximación — lejos
    de la línea calibrada (ej. evaluar 0.5 cuando Pinnacle solo cotizó
    2.5) el modelo pierde precisión, y un EV absurdamente alto suele
    ser eso, no valor real. Mismo espíritu que EV_MAX en sharp_ev.py."""
    por_casa = _precios_totals_por_casa(event)
    if ancla not in por_casa or len(por_casa) < min_books:
        return None

    # Línea "principal" de Pinnacle = la de cuotas más parejas entre sí
    # (si por algún motivo cotiza más de una).
    linea_pin, precios_pin = min(
        por_casa[ancla].items(), key=lambda kv: abs(kv[1]["Over"] - kv[1]["Under"]))
    fair_pin = sharp_ev._novig(precios_pin)
    if len(fair_pin) != 2:
        return None

    lam = _lambda_desde_linea(linea_pin, fair_pin["Over"])
    if lam is None:
        return None

    todas_las_lineas = {l for lineas in por_casa.values() for l in lineas}

    mejor: PataTotals | None = None
    for linea in todas_las_lineas:
        for lado in _RESULTADOS:
            fair_prob = _prob_en_linea(lam, linea, lado)
            if fair_prob < prob_min:
                continue

            mejor_odds, mejor_book = 0.0, ""
            for casa, lineas_casa in por_casa.items():
                if casa == ancla or linea not in lineas_casa:
                    continue
                p = lineas_casa[linea].get(lado, 0.0)
                if p > mejor_odds:
                    mejor_odds, mejor_book = p, casa
            if mejor_odds <= 1.0:
                continue

            fair_odds = 1.0 / fair_prob
            if 1.0 - (mejor_odds / fair_odds) > max_descuento:
                continue

            ev = fair_prob * mejor_odds - 1.0
            if ev > ev_max:
                continue

            candidato = PataTotals(
                sport=event.get("sport_key", ""), event_id=event.get("id", ""),
                home=event["home_team"], away=event["away_team"],
                commence_time=event.get("commence_time", ""),
                outcome=f"{lado.lower()}_{linea}",
                fair_prob=round(fair_prob, 4),
                ancla_odds=round(fair_odds, 3),
                best_odds=round(mejor_odds, 3), best_book=mejor_book,
                ev=round(ev, 4), n_books=len(por_casa),
            )
            if mejor is None or candidato.fair_prob > mejor.fair_prob:
                mejor = candidato
    return mejor


def liquidar_outcome(outcome: str, goles_local: int, goles_visitante: int) -> str:
    """WIN/LOSS de un outcome 'over_X.X' / 'under_X.X' contra el marcador
    final. Las líneas de este mercado son siempre x.5 — no hay push."""
    _, linea_str = outcome.split("_", 1)
    linea = float(linea_str)
    total = goles_local + goles_visitante
    es_over = total > linea
    return "WIN" if (outcome.startswith("over_") == es_over) else "LOSS"

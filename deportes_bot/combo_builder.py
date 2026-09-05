# ============================================================
#  combo_builder.py — Combinadas seguras ancladas en Pinnacle
#
#  Creado por Diego Aleman.
#
#  Reemplaza al enfoque de ai_analyzer.py para armar combinadas:
#  ahí GPT "estimaba" probabilidades a ojo (incluso para mercados
#  sin ningún dato real, como corners o tarjetas). Acá cada pata
#  sale del mismo método que sharp_ev.py: el no-vig de Pinnacle,
#  el mercado más eficiente que existe — no una cuota alta, y no
#  una adivinanza de un modelo de lenguaje.
#
#  Filosofía: preferir 70-85% de probabilidad REAL de mercado a
#  una cuota llamativa. Como máximo una pata por partido (con 3
#  resultados que suman 1, solo uno puede superar un umbral > 50%),
#  y las combinadas solo cruzan partidos de ligas distintas para
#  no apilar patas correlacionadas entre sí.
#
#  La probabilidad combinada de la multiplicación simple SIEMPRE
#  sobreestima la seguridad real si hay algo de correlación entre
#  patas (mismo día, tendencias de mercado compartidas, etc.) —
#  por eso se aplica un descuento conservador, y por eso el
#  acierto real se mide en clv_db.resumen_combos() en vez de
#  confiar ciegamente en la probabilidad prometida.
# ============================================================

from dataclasses import dataclass
from itertools import combinations

import sharp_ev


@dataclass
class PataSegura:
    sport: str
    event_id: str
    home: str
    away: str
    commence_time: str
    outcome: str            # local | empate | visitante
    fair_prob: float        # prob. justa (no-vig Pinnacle) del outcome elegido
    ancla_odds: float       # cuota de Pinnacle para ese outcome (con margen)
    best_odds: float        # mejor cuota disponible en casa blanda
    best_book: str
    ev: float                # fair_prob * best_odds - 1 (normalmente ≤ 0: se paga margen por la seguridad)
    n_books: int


@dataclass
class ComboPropuesto:
    tipo: str                  # "2 patas" | "3 patas"
    patas: list[PataSegura]
    cuota_total: float
    prob_producto: float       # multiplicación simple de las fair_prob (sobreestima si hay correlación)
    prob_ajustada: float       # con descuento conservador por correlación
    ev_combo: float             # prob_ajustada * cuota_total - 1


def pata_segura_evento(event: dict, prob_min: float = 0.70,
                       min_books: int = 6, max_descuento: float = 0.08,
                       ancla: str = sharp_ev.ANCLA) -> PataSegura | None:
    """La pata más segura de UN evento (a lo sumo una: con 3 outcomes
    que suman 1, solo uno puede superar un umbral > 50%).

    None si no hay ancla, faltan casas, el outcome más probable no
    llega al umbral, o la casa blanda cobra demasiado margen extra
    por esa "seguridad" (favoritos muy claros suelen venir con vig
    inflado — max_descuento pone un techo a cuánto se tolera pagar
    de más contra la cuota justa de Pinnacle)."""
    por_casa = sharp_ev._precios_por_casa(event)
    if ancla not in por_casa or len(por_casa) < min_books:
        return None

    fair = sharp_ev._novig(por_casa[ancla])
    if len(fair) != 3:
        return None

    outcome, fair_prob = max(fair.items(), key=lambda kv: kv[1])
    if fair_prob < prob_min:
        return None

    best_odds, best_book = sharp_ev.mejor_precio(por_casa, outcome)
    if best_odds <= 1.0:
        return None

    fair_odds = 1.0 / fair_prob
    descuento_real = 1.0 - (best_odds / fair_odds)
    if descuento_real > max_descuento:
        return None

    return PataSegura(
        sport=event.get("sport_key", ""),
        event_id=event.get("id", ""),
        home=event["home_team"], away=event["away_team"],
        commence_time=event.get("commence_time", ""),
        outcome=outcome,
        fair_prob=round(fair_prob, 4),
        ancla_odds=round(por_casa[ancla][outcome], 3),
        best_odds=round(best_odds, 3),
        best_book=best_book,
        ev=round(fair_prob * best_odds - 1.0, 4),
        n_books=len(por_casa),
    )


def _producto(vals) -> float:
    r = 1.0
    for v in vals:
        r *= v
    return r


def armar_combos(patas: list, tamanos: tuple[int, ...] = (2, 3),
                 max_por_tamano: int = 2,
                 descuento_correlacion: float = 0.04,
                 exigir_ligas_distintas: bool = True) -> list[ComboPropuesto]:
    """
    `patas` puede mezclar PataSegura (1X2) y PataTotals (goles, ver
    totals_ev.py) — ambas exponen los mismos campos clave (sport,
    event_id, fair_prob, best_odds), así que se combinan sin problema.
    exigir_ligas_distintas ya evita mezclar dos patas del MISMO partido
    (comparten sport) aunque sean de mercados distintos.

    Arma varias combinadas distintas (no solo una), priorizando las
    patas de mayor probabilidad y — si exigir_ligas_distintas — sin
    repetir liga dentro de la misma combinada, para minimizar
    correlación entre patas.

    descuento_correlacion: fracción que se resta de la probabilidad
    combinada por cada pata además de la primera (ej. 0.04 con 3
    patas → ~8% menos que la multiplicación simple). Es una
    aproximación conservadora, no un cálculo de correlación real
    — el objetivo es no vender como "95% seguro" algo que la
    multiplicación simple exagera.
    """
    patas_ordenadas = sorted(patas, key=lambda p: p.fair_prob, reverse=True)
    combos: list[ComboPropuesto] = []
    usados: set[frozenset] = set()

    for tam in tamanos:
        if len(patas_ordenadas) < tam:
            continue
        generadas = 0
        for combo_patas in combinations(patas_ordenadas, tam):
            if exigir_ligas_distintas and len({p.sport for p in combo_patas}) < tam:
                continue

            ids = frozenset(p.event_id for p in combo_patas)
            if ids in usados:
                continue

            prob_prod = _producto(p.fair_prob for p in combo_patas)
            descuento = min(0.30, descuento_correlacion * (tam - 1))
            prob_ajustada = round(prob_prod * (1 - descuento), 4)
            cuota_total = round(_producto(p.best_odds for p in combo_patas), 3)

            combos.append(ComboPropuesto(
                tipo=f"{tam} patas",
                patas=list(combo_patas),
                cuota_total=cuota_total,
                prob_producto=round(prob_prod, 4),
                prob_ajustada=prob_ajustada,
                ev_combo=round(prob_ajustada * cuota_total - 1.0, 4),
            ))
            usados.add(ids)
            generadas += 1
            if generadas >= max_por_tamano:
                break

    return sorted(combos, key=lambda c: c.prob_ajustada, reverse=True)

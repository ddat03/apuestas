# ============================================================
#  confidence_compositor.py — Score de confianza compuesto
#
#  Combina probabilidad base (bookmaker consensus) con
#  todos los factores contextuales en un score final:
#
#  Score = prob_base
#        + forma.bonus          (-5% → +5%)
#        + h2h.bonus_local      (-4% → +4%)
#        + ctx.bonus_descanso   (-3% → +3%)
#        + plantilla.penalizacion (-25% → 0%)
#        + sentimiento_factor   (-3% → +3%)
#  → Normalizado a [0.35, 0.97]
#  → Solo se recomienda si Score ≥ UMBRAL_CONFIANZA (default 0.60)
# ============================================================

from dataclasses import dataclass

from player_analyzer import EstadoPlantilla
from sentiment_analyzer import SentimientoPartido
from stats_analyzer import ContextoPartido, FormaEquipo, H2H

UMBRAL_CONFIANZA = 0.60   # filtro mínimo


@dataclass
class ScoreCompuesto:
    prob_base:        float
    bonus_forma:      float
    bonus_h2h:        float
    bonus_descanso:   float
    pen_plantilla_l:  float
    pen_plantilla_v:  float
    bonus_sentimiento: float
    score_final:      float
    supera_umbral:    bool
    desglose:         str


def calcular(prob_base: float,
             forma_l: FormaEquipo | None   = None,
             forma_v: FormaEquipo | None   = None,
             h2h:     H2H | None           = None,
             ctx:     ContextoPartido | None = None,
             plantilla_l: EstadoPlantilla | None = None,
             plantilla_v: EstadoPlantilla | None = None,
             sentimiento: SentimientoPartido | None = None,
             es_local: bool = True) -> ScoreCompuesto:
    """
    Calcula el score compuesto para la selección indicada.

    es_local: True si la apuesta es a favor del equipo local,
              False si es al visitante (invierte los bonus de forma/plantilla).
    """
    bonus_forma     = 0.0
    bonus_h2h       = 0.0
    bonus_descanso  = 0.0
    pen_l           = 0.0
    pen_v           = 0.0
    bonus_sent      = 0.0

    # Forma reciente
    if forma_l:
        bonus_forma += forma_l.bonus * (1 if es_local else -1)
    if forma_v:
        bonus_forma -= forma_v.bonus * (1 if es_local else -1)

    # Head-to-head
    if h2h:
        bonus_h2h = h2h.bonus_local * (1 if es_local else -1)

    # Descanso
    if ctx:
        bonus_descanso = ctx.bonus_descanso * (1 if es_local else -1)

    # Plantilla (siempre negativo para el equipo favorecido)
    if plantilla_l and es_local:
        pen_l = plantilla_l.penalizacion
    if plantilla_v and not es_local:
        pen_v = plantilla_v.penalizacion

    # Sentimiento
    if sentimiento and sentimiento.disponible:
        if es_local:
            bonus_sent = sentimiento.score_local * 0.03
        else:
            bonus_sent = sentimiento.score_visita * 0.03

    score = (prob_base
             + bonus_forma
             + bonus_h2h
             + bonus_descanso
             + pen_l
             + pen_v
             + bonus_sent)

    score = max(0.35, min(0.97, score))

    partes = [f"Base={prob_base:.0%}"]
    if abs(bonus_forma)    > 0.001: partes.append(f"Forma{bonus_forma:+.1%}")
    if abs(bonus_h2h)      > 0.001: partes.append(f"H2H{bonus_h2h:+.1%}")
    if abs(bonus_descanso) > 0.001: partes.append(f"Desc{bonus_descanso:+.1%}")
    if pen_l               < -0.001: partes.append(f"PlantillaL{pen_l:+.1%}")
    if pen_v               < -0.001: partes.append(f"PlantillaV{pen_v:+.1%}")
    if abs(bonus_sent)     > 0.001: partes.append(f"News{bonus_sent:+.1%}")
    partes.append(f"→ FINAL={score:.0%}")

    return ScoreCompuesto(
        prob_base         = prob_base,
        bonus_forma       = bonus_forma,
        bonus_h2h         = bonus_h2h,
        bonus_descanso    = bonus_descanso,
        pen_plantilla_l   = pen_l,
        pen_plantilla_v   = pen_v,
        bonus_sentimiento = bonus_sent,
        score_final       = score,
        supera_umbral     = score >= UMBRAL_CONFIANZA,
        desglose          = " | ".join(partes),
    )


def filtrar_por_score(apuestas: list[dict],
                       forma_cache: dict,
                       h2h_cache:   dict,
                       ctx_cache:   dict,
                       plantilla_cache: dict,
                       sentimiento_cache: dict) -> list[dict]:
    """
    Recalcula el score compuesto para cada apuesta y filtra
    las que no superan UMBRAL_CONFIANZA.

    apuestas: lista de dicts con campos partido_id, prob_base, es_local
    *_cache: dicts keyed por partido_id

    Devuelve la lista filtrada con el campo 'score_compuesto' añadido.
    """
    resultado = []
    for ap in apuestas:
        pid = ap.get("partido_id")
        sc = calcular(
            prob_base    = ap.get("prob_base", 0.5),
            forma_l      = forma_cache.get(pid, {}).get("local"),
            forma_v      = forma_cache.get(pid, {}).get("visita"),
            h2h          = h2h_cache.get(pid),
            ctx          = ctx_cache.get(pid),
            plantilla_l  = plantilla_cache.get(pid, {}).get("local"),
            plantilla_v  = plantilla_cache.get(pid, {}).get("visita"),
            sentimiento  = sentimiento_cache.get(pid),
            es_local     = ap.get("es_local", True),
        )
        ap["score_compuesto"] = sc
        if sc.supera_umbral:
            resultado.append(ap)

    return sorted(resultado, key=lambda x: x["score_compuesto"].score_final, reverse=True)

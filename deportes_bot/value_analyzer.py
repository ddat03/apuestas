# ============================================================
#  value_analyzer.py — Análisis de valor (Solo modo SHARP)
#
#  Usa únicamente probabilidades consenso del mercado
#  (25+ bookmakers sin margen). El ML no se usa en predict.
# ============================================================

import logging
from datetime import datetime

import pandas as pd

from config import (
    KELLY_FRACTION, BANKROLL_INICIAL, MAX_APUESTA_PCT,
    CSV_RECOMENDACIONES, LOG_FILE
)

log = logging.getLogger("ValueAnalyzer")


# ══════════════════════════════════════════
#  UTILIDADES
# ══════════════════════════════════════════

def cuota_a_prob(cuota: float) -> float:
    if not cuota or cuota <= 1.0:
        return 0.0
    return round(1.0 / cuota, 4)


def valor_esperado(mi_prob: float, cuota: float) -> float:
    if not cuota or cuota <= 1:
        return -1.0
    return round((mi_prob * cuota) - 1.0, 4)


def kelly_fraction_apuesta(mi_prob: float, cuota: float, bankroll: float) -> float:
    b = cuota - 1
    q = 1 - mi_prob
    if b <= 0:
        return 0.0
    kelly = (mi_prob * b - q) / b
    max_bet = bankroll * MAX_APUESTA_PCT
    sugerida = max(0.0, kelly * KELLY_FRACTION * bankroll)
    return round(min(sugerida, max_bet), 2)


def margen_casa(c_local: float, c_empate: float, c_visitante: float) -> float:
    probs = [cuota_a_prob(c) for c in [c_local, c_empate, c_visitante] if c and c > 1]
    return round(sum(probs) - 1.0, 4) if probs else 0.0


# ══════════════════════════════════════════
#  ANALIZADOR SHARP (único modo en predict)
# ══════════════════════════════════════════

def analizar_partido(partido: pd.Series | dict,
                     bankroll: float = BANKROLL_INICIAL) -> dict:
    """
    Analiza UN partido usando consenso de bookmakers (modo SHARP).
    No usa ML. Evalúa los 3 outcomes y detecta cualquier EV positivo.
    """
    c_local     = float(partido.get("cuota_local",     0) or 0)
    c_empate    = float(partido.get("cuota_empate",    0) or 0)
    c_visitante = float(partido.get("cuota_visitante", 0) or 0)
    n_bk        = int(partido.get("n_bookmakers",      1) or 1)

    # Probabilidades consenso (ya sin margen, calculadas en data_collector)
    p_local     = float(partido.get("cons_prob_local",     0) or 0)
    p_empate    = float(partido.get("cons_prob_empate",    0) or 0)
    p_visitante = float(partido.get("cons_prob_visitante", 0) or 0)

    # Fallback: normalizar desde cuotas si no hay consenso
    if not p_local:
        suma = cuota_a_prob(c_local) + cuota_a_prob(c_empate) + cuota_a_prob(c_visitante)
        if suma:
            p_local     = round(cuota_a_prob(c_local)     / suma, 4)
            p_empate    = round(cuota_a_prob(c_empate)    / suma, 4)
            p_visitante = round(cuota_a_prob(c_visitante) / suma, 4)
        else:
            p_local, p_empate, p_visitante = 0.33, 0.33, 0.34

    # Confianza basada en liquidez del mercado (más bookmakers = más fiable)
    confianza = min(0.92, 0.55 + n_bk * 0.015)

    # Evaluar EV de los 3 outcomes
    candidatos = [
        ("Local",     p_local,     c_local,     cuota_a_prob(c_local)),
        ("Empate",    p_empate,    c_empate,    cuota_a_prob(c_empate)),
        ("Visitante", p_visitante, c_visitante, cuota_a_prob(c_visitante)),
    ]

    evs = [
        (nombre, prob, cuota, prob_imp, valor_esperado(prob, cuota))
        for nombre, prob, cuota, prob_imp in candidatos
        if cuota and cuota > 1.0
    ]

    # Outcome con mayor EV positivo
    ev_positivos = [(n, p, c, pm, ev) for n, p, c, pm, ev in evs if ev > 0]

    if ev_positivos:
        prediccion, mi_prob, cuota_bet, prob_mercado, ev = max(ev_positivos, key=lambda x: x[4])
    else:
        # Sin valor: reportar el favorito del consenso
        prediccion, mi_prob, cuota_bet, prob_mercado = max(candidatos, key=lambda x: x[1])
        ev = valor_esperado(mi_prob, cuota_bet)

    apuesta = kelly_fraction_apuesta(mi_prob, cuota_bet, bankroll)
    score   = round(max(0.0, min(100.0, ev * confianza * 150)), 2) if ev > 0 else 0.0

    # Clasificación de confianza
    if ev >= 0.08 and confianza >= 0.72:
        recomendacion = "APOSTAR"
        nivel = "Alta"
    elif ev >= 0.04 and confianza >= 0.60:
        recomendacion = "APOSTAR"
        nivel = "Media"
    elif ev >= 0.015:
        recomendacion = "CONSIDERAR"
        nivel = "Baja"
    elif ev > 0:
        recomendacion = "ESPERAR"
        nivel = "Muy baja"
    else:
        recomendacion = "NO"
        nivel = "-"

    razon = (
        f"[SHARP/{n_bk} bk] {prediccion} @ {cuota_bet} | "
        f"consenso={mi_prob:.1%} | EV={ev:+.3f} | confianza={nivel}"
    )

    return {
        "fecha":            partido.get("fecha", ""),
        "liga":             partido.get("liga_nombre", ""),
        "partido":          f"{partido.get('equipo_local','')} vs {partido.get('equipo_visitante','')}",
        "equipo_local":     partido.get("equipo_local", ""),
        "equipo_visitante": partido.get("equipo_visitante", ""),
        "mi_prediccion":    prediccion,
        "local_prob":       p_local,
        "draw_prob":        p_empate,
        "away_prob":        p_visitante,
        "confianza":        confianza,
        "nivel_confianza":  nivel,
        "cuota_apuesta":    cuota_bet,
        "prob_mercado":     prob_mercado,
        "ev":               ev,
        "n_bookmakers":     n_bk,
        "score":            score,
        "apuesta_sugerida": apuesta,
        "recomendacion":    recomendacion,
        "razon":            razon,
        "cuota_local":      c_local,
        "cuota_empate":     c_empate,
        "cuota_visitante":  c_visitante,
    }


def analizar_todos(df_partidos: pd.DataFrame,
                   bankroll: float = BANKROLL_INICIAL) -> pd.DataFrame:
    """
    Analiza todos los partidos en modo SHARP.
    Devuelve TODOS los partidos ordenados por EV descendente (sin límite).
    """
    log.info("Analizando valor en partidos (modo SHARP)…")
    resultados = [analizar_partido(row, bankroll) for _, row in df_partidos.iterrows()]

    df = pd.DataFrame(resultados)
    df = df.sort_values("ev", ascending=False).reset_index(drop=True)

    CSV_RECOMENDACIONES.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV_RECOMENDACIONES, index=False, encoding="utf-8")
    log.info(f"✓ Recomendaciones guardadas en {CSV_RECOMENDACIONES}")

    n_apostar    = len(df[df["recomendacion"] == "APOSTAR"])
    n_considerar = len(df[df["recomendacion"] == "CONSIDERAR"])
    log.info(f"Oportunidades: {n_apostar} APOSTAR + {n_considerar} CONSIDERAR (de {len(df)} partidos)")

    return df


def apuestas_con_valor(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve todos los partidos con EV positivo (APOSTAR + CONSIDERAR)."""
    return df[df["recomendacion"].isin(["APOSTAR", "CONSIDERAR"])].reset_index(drop=True)

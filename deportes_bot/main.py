#!/usr/bin/env python3
# ============================================================
#  main.py — Coordinador principal del sistema
#
#  Modos:
#    train    → descarga histórico + entrena modelo
#    predict  → predice partidos de hoy y envía Telegram
#    backtest → simula apuestas en período histórico
#    demo     → modo completo sin API (datos mock)
#    tracker  → abre dashboard de resultados
# ============================================================

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import (
    CSV_PARTIDOS, CSV_RECOMENDACIONES, CSV_HISTORICO, MODEL_PATH,
    BANKROLL_INICIAL, LOG_FILE, MOCK_MODE
)

# ─────────────────────────────────────────
#  Logger global
# ─────────────────────────────────────────
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("Main")


# ══════════════════════════════════════════
#  MODO: ENTRENAMIENTO
# ══════════════════════════════════════════

def modo_train():
    """
    1. (Opcional) Descarga datos históricos de la API.
    2. Entrena y guarda el modelo.
    """
    from ml_model import entrenar_modelo

    log.info("═" * 55)
    log.info("MODO ENTRENAMIENTO")
    log.info("═" * 55)

    # Si existe el CSV histórico (con columna 'resultado') úsalo,
    # si no el modelo genera datos sintéticos automáticamente
    df_historico = None
    if CSV_HISTORICO.exists():
        log.info(f"Cargando datos históricos desde {CSV_HISTORICO}")
        df_historico = pd.read_csv(CSV_HISTORICO)

    metricas = entrenar_modelo(df_historico)

    print("\n" + "═" * 55)
    print("ENTRENAMIENTO COMPLETADO")
    print("═" * 55)
    print(f"  Mejor modelo  : {metricas['mejor_modelo']}")
    print(f"  CV Accuracy   : {metricas['cv_accuracy']:.3f}")
    print(f"  Modelo guardado en: {MODEL_PATH}")
    print("═" * 55)


# ══════════════════════════════════════════
#  MODO: PREDICCIÓN DIARIA
# ══════════════════════════════════════════

def modo_predict(bankroll: float = BANKROLL_INICIAL,
                 enviar_telegram: bool = True,
                 usar_ia: bool = True):
    """
    Flujo completo de predicción diaria (sin ML):
    1. Recolecta partidos con cuotas reales
    2. Descarga mercados adicionales (O/U, BTTS)
    3. Análisis SHARP: EV = mejor_cuota × prob_consenso - 1
    4. Análisis IA (OpenAI): más mercados + combinadas
    5. Envía Telegram
    """
    from data_collector    import recolectar_datos
    from value_analyzer    import analizar_todos
    from markets_collector import recolectar_mercados, mercados_a_dict, descargar_todos_mercados
    from ai_analyzer       import analizar_con_ia, formatear_analisis_ia
    from telegram_bot      import enviar_recomendaciones_diarias, enviar_mensaje
    from config            import LIGAS_PERMITIDAS

    log.info("═" * 55)
    log.info("MODO PREDICCIÓN DIARIA (SHARP + IA)")
    log.info(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info("═" * 55)

    # ── 1. Recolectar partidos ────────────────────────────
    log.info("[1/5] Recolectando partidos…")
    df_partidos = recolectar_datos()
    if df_partidos.empty:
        log.error("No se encontraron partidos. Abortando.")
        return

    # ── 2. Mercados adicionales (1 request por liga) ──────
    log.info("[2/5] Descargando mercados adicionales (O/U, BTTS)…")
    cache_por_liga: dict[int, list] = {}
    for liga_id in LIGAS_PERMITIDAS:
        cache_por_liga[liga_id] = descargar_todos_mercados(liga_id)

    lista_mercados_dict = []
    for _, row in df_partidos.iterrows():
        liga_id = int(row.get("liga_id", 1))
        cache   = cache_por_liga.get(liga_id, [])
        pm = recolectar_mercados(row.to_dict(), liga_id, eventos_cache=cache)
        lista_mercados_dict.append(mercados_a_dict(pm))

    # ── 3. Análisis SHARP ─────────────────────────────────
    log.info("[3/5] Análisis SHARP (consenso de bookmakers)…")
    df_analisis = analizar_todos(df_partidos, bankroll=bankroll)
    _imprimir_resumen(df_analisis)

    # ── 4. Análisis IA (OpenAI) ───────────────────────────
    analisis_ia = {}
    msg_ia      = ""
    if usar_ia and os.getenv("OPENAI_API_KEY", ""):
        log.info("[4/5] Analizando con OpenAI…")
        analisis_ia = analizar_con_ia(
            df_partidos.to_dict("records"),
            lista_mercados_dict,
            bankroll=bankroll,
            max_apuesta=bankroll * 0.05,
        )
        msg_ia = formatear_analisis_ia(analisis_ia)
        print("\n" + msg_ia)
    else:
        log.info("[4/5] IA omitida (sin OPENAI_API_KEY)")

    # ── 5. Telegram (UN solo mensaje) ────────────────────
    if enviar_telegram:
        log.info("[5/5] Enviando Telegram…")
        if msg_ia:
            enviar_mensaje(msg_ia)
        else:
            # Fallback si OpenAI no está disponible
            enviar_recomendaciones_diarias(df_analisis)
    else:
        log.info("[5/5] Telegram desactivado")

    return df_analisis, analisis_ia


# ══════════════════════════════════════════
#  MODO: BACKTEST
# ══════════════════════════════════════════

def modo_backtest(start: str, end: str, bankroll: float = BANKROLL_INICIAL):
    """
    Simula qué habría pasado apostando según las recomendaciones
    del modelo en un período histórico.

    start / end: formato YYYY-MM-DD
    """
    from ml_model       import predecir_todos, cargar_modelo, preparar_features, entrenar_modelo
    from value_analyzer import analizar_todos, top_recomendaciones
    from config         import APUESTAS_MAXIMAS_DIA

    log.info("═" * 55)
    log.info(f"MODO BACKTEST: {start} → {end}")
    log.info("═" * 55)

    # Cargar histórico
    if not CSV_PARTIDOS.exists():
        log.error(f"No existe {CSV_PARTIDOS}. Necesitas datos históricos.")
        return

    df_all = pd.read_csv(CSV_PARTIDOS)
    df_all["fecha"] = pd.to_datetime(df_all["fecha"])
    mask = (df_all["fecha"] >= start) & (df_all["fecha"] <= end)
    df_periodo = df_all[mask].copy()

    if df_periodo.empty:
        log.error(f"Sin datos en el período {start} → {end}")
        return

    log.info(f"Partidos en el período: {len(df_periodo)}")

    # Entrenar o cargar modelo
    if not MODEL_PATH.exists():
        log.info("Entrenando modelo (se excluirán datos del período para evitar leakage)…")
        df_train = df_all[df_all["fecha"] < start].copy()
        entrenar_modelo(df_train if not df_train.empty else None)

    # Simular día a día
    cap = bankroll
    historico_bt = []
    dias = df_periodo["fecha"].dt.date.unique()

    for dia in sorted(dias):
        df_dia = df_periodo[df_periodo["fecha"].dt.date == dia].copy()
        if df_dia.empty:
            continue

        df_preds = predecir_todos(df_dia)
        df_analisis = analizar_todos(df_dia, df_preds, bankroll=cap)
        top = top_recomendaciones(df_analisis, n=APUESTAS_MAXIMAS_DIA)

        for _, rec in top.iterrows():
            monto  = float(rec.get("apuesta_sugerida", 0))
            if monto <= 0:
                continue

            # Simular resultado (si hay columna "resultado_real" en datos históricos)
            resultado_real = rec.get("resultado_real", "UNKNOWN")
            prediccion     = rec.get("mi_prediccion", "")
            cuota          = float(rec.get("cuota_apuesta", 2.0))

            if resultado_real == "UNKNOWN":
                # Si no hay resultado real, usar probabilidades del modelo para simular
                import random
                rng = random.Random(hash(str(dia) + str(rec.get("partido",""))))
                prob_ok = float(rec.get("confianza", 0.5))
                ganó = rng.random() < prob_ok
            else:
                ganó = str(resultado_real).strip().upper() == prediccion.upper()[:3]

            if ganó:
                ganancia = round(monto * (cuota - 1), 2)
                resultado = "WIN"
            else:
                ganancia = -monto
                resultado = "LOSS"

            cap += ganancia
            historico_bt.append({
                "fecha":     dia,
                "partido":   rec.get("partido",""),
                "prediccion":prediccion,
                "cuota":     cuota,
                "monto":     monto,
                "resultado": resultado,
                "ganancia":  ganancia,
                "capital":   round(cap, 2),
                "score":     rec.get("score", 0),
            })

    if not historico_bt:
        log.warning("Sin apuestas generadas en el backtest (ningún partido pasó los filtros)")
        return

    df_bt = pd.DataFrame(historico_bt)
    apostado_total = df_bt["monto"].sum()
    ganado_total   = df_bt["ganancia"].sum()
    roi_total      = (ganado_total / apostado_total * 100) if apostado_total else 0
    wins           = (df_bt["resultado"] == "WIN").sum()
    winrate        = wins / len(df_bt) * 100

    print("\n" + "═" * 55)
    print(f"RESULTADO BACKTEST: {start} → {end}")
    print("═" * 55)
    print(f"  Capital inicial : {bankroll:.2f}")
    print(f"  Capital final   : {cap:.2f}")
    print(f"  Total apostado  : {apostado_total:.2f}")
    print(f"  Ganancia/Pérdida: {ganado_total:+.2f}")
    print(f"  ROI             : {roi_total:+.2f}%")
    print(f"  Win rate        : {winrate:.1f}%")
    print(f"  Nº apuestas     : {len(df_bt)}")
    print("═" * 55)

    # Guardar resultado
    out = Path("reports") / f"backtest_{start}_{end}.csv"
    out.parent.mkdir(exist_ok=True)
    df_bt.to_csv(out, index=False)
    log.info(f"✓ Backtest guardado en {out}")

    return df_bt


# ══════════════════════════════════════════
#  MODO: TRACKER / DASHBOARD
# ══════════════════════════════════════════

def modo_tracker(graficos: bool = False):
    """Muestra estadísticas de apuestas registradas."""
    from bet_tracker import imprimir_dashboard, generar_graficos, inicializar_db
    inicializar_db()
    imprimir_dashboard()
    if graficos:
        generar_graficos()


# ══════════════════════════════════════════
#  MODO: DEMO (sin APIs)
# ══════════════════════════════════════════

def modo_demo():
    """
    Modo completo sin APIs ni claves.
    Usa datos mock para demostrar el flujo entero.
    """
    log.info("MODO DEMO — todos los datos son ficticios")
    modo_train()
    modo_predict(enviar_telegram=True)


# ══════════════════════════════════════════
#  UTILIDADES DE PRESENTACIÓN
# ══════════════════════════════════════════

def _imprimir_resumen(df: pd.DataFrame):
    cols_show = ["partido", "liga", "mi_prediccion", "confianza",
                 "cuota_apuesta", "edge", "ev", "score", "recomendacion"]
    cols_ok   = [c for c in cols_show if c in df.columns]
    top = df[df["recomendacion"].isin(["APOSTAR","CONSIDERAR"])].head(10)

    print("\n" + "═" * 70)
    print("  RECOMENDACIONES DEL DÍA")
    print("═" * 70)
    if top.empty:
        print("  Sin oportunidades con valor suficiente hoy.")
    else:
        for _, row in top.iterrows():
            print(
                f"  [{row.get('recomendacion','?'):10s}] "
                f"{row.get('partido',''):35s} "
                f"score={row.get('score',0):5.1f} "
                f"ev={row.get('ev',0):+.3f}"
            )
    print("═" * 70 + "\n")


# ══════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Sistema de análisis deportivo para apuestas personales",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["train", "predict", "backtest", "demo", "tracker"],
        default="demo",
        help=(
            "train    → entrena el modelo ML\n"
            "predict  → predicciones del día (requiere API keys)\n"
            "backtest → simula período histórico\n"
            "demo     → demo completo sin APIs\n"
            "tracker  → muestra dashboard de resultados"
        ),
    )
    parser.add_argument("--start",         default="2024-01-01", help="Fecha inicio backtest (YYYY-MM-DD)")
    parser.add_argument("--end",           default="2024-06-30", help="Fecha fin backtest (YYYY-MM-DD)")
    parser.add_argument("--bankroll",      type=float, default=BANKROLL_INICIAL, help="Capital disponible")
    parser.add_argument("--no-telegram",   action="store_true",  help="No enviar Telegram")
    parser.add_argument("--graficos",      action="store_true",  help="Generar gráficos (modo tracker)")

    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════╗
║   SISTEMA DE ANÁLISIS DEPORTIVO v1.0     ║
║   Modo: {args.mode:<34}║
╚══════════════════════════════════════════╝
""")

    if args.mode == "train":
        modo_train()

    elif args.mode == "predict":
        modo_predict(
            bankroll=args.bankroll,
            enviar_telegram=not args.no_telegram,
        )

    elif args.mode == "backtest":
        modo_backtest(
            start=args.start,
            end=args.end,
            bankroll=args.bankroll,
        )

    elif args.mode == "demo":
        modo_demo()

    elif args.mode == "tracker":
        modo_tracker(graficos=args.graficos)


if __name__ == "__main__":
    main()

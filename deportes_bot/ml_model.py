# ============================================================
#  ml_model.py — Entrenamiento y predicción con ML
#
#  Algoritmos: Random Forest + Gradient Boosting
#  Target:     resultado (1=local, 0=empate, -1=visitante)
# ============================================================

import logging
import pickle
import json
from pathlib import Path
from io import StringIO

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import (
    accuracy_score, classification_report, confusion_matrix
)

from config import (
    MODEL_PATH, SCALER_PATH, REPORT_PATH,
    CSV_HISTORICO, MODELS_DIR, REPORTS_DIR, LOG_FILE
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("MLModel")

# ─────────────────────────────────────────
#  FEATURES usadas en el modelo
# ─────────────────────────────────────────
FEATURES = [
    "gf_local_5j",       # goles a favor del local (prom. 5j)
    "gc_local_5j",       # goles en contra del local (prom. 5j)
    "gf_visitante_5j",   # goles a favor del visitante (prom. 5j)
    "gc_visitante_5j",   # goles en contra del visitante (prom. 5j)
    "forma_local_pts",   # puntos de forma local (W=3, D=1, L=0) / 5j
    "forma_visit_pts",   # puntos de forma visitante
    "h2h_home_wins",     # victorias local en H2H
    "h2h_draws",         # empates H2H
    "h2h_away_wins",     # victorias visitante H2H
    "diferencia_gf",     # gf_local - gf_visitante
    "diferencia_gc",     # gc_local - gc_visitante  (negativo = local más sólido)
    "factor_local",      # siempre 1 (feature constante pero útil en ensembles)
]

TARGET = "resultado"   # 1=local, 0=empate, -1=visitante
LABEL_MAP = {1: "Local", 0: "Empate", -1: "Visitante"}


# ══════════════════════════════════════════
#  GENERADOR DE DATOS SINTÉTICOS DE ENTRENAMIENTO
# ══════════════════════════════════════════

def _generar_datos_sinteticos(n: int = 5000, seed: int = 42) -> pd.DataFrame:
    """
    Genera un dataset sintético pero estadísticamente plausible para
    entrenar el modelo cuando no hay datos históricos reales.

    Las correlaciones son deliberadas:
      - gf_local_5j alto → mayor prob local
      - gc_local_5j alto → menor prob local
      - h2h_home_wins    → sesgo local
    """
    rng = np.random.default_rng(seed)
    log.info(f"Generando {n} partidos sintéticos para entrenamiento…")

    gf_h = rng.uniform(0.5, 3.0, n)
    gc_h = rng.uniform(0.3, 2.5, n)
    gf_a = rng.uniform(0.5, 2.8, n)
    gc_a = rng.uniform(0.3, 2.5, n)
    forma_h = rng.uniform(0, 9, n)     # 0-9 puntos en 3 últimas (W=3,D=1)
    forma_a = rng.uniform(0, 9, n)
    h2h_h   = rng.integers(0, 8, n)
    h2h_d   = rng.integers(0, 4, n)
    h2h_a   = rng.integers(0, 8, n)

    # Probabilidad de victoria local (regla heurística con ruido)
    p_local = (
        0.30
        + 0.08 * gf_h
        - 0.06 * gc_h
        - 0.06 * gf_a
        + 0.05 * gc_a
        + 0.02 * forma_h
        - 0.02 * forma_a
        + 0.01 * h2h_h
        - 0.01 * h2h_a
        + 0.05                     # factor casa fijo
        + rng.normal(0, 0.05, n)  # ruido
    ).clip(0.1, 0.85)

    p_visitante = (
        0.20
        - 0.05 * gf_h
        + 0.07 * gc_h
        + 0.07 * gf_a
        - 0.04 * gc_a
        - 0.01 * forma_h
        + 0.02 * forma_a
        + rng.normal(0, 0.05, n)
    ).clip(0.05, 0.75)

    p_empate = (1 - p_local - p_visitante).clip(0.05, 0.50)

    # Normalizar a que sumen 1
    total  = p_local + p_visitante + p_empate
    p_local      /= total
    p_visitante  /= total
    p_empate     /= total

    # Simular resultado
    resultados = []
    for pl, pd_, pa in zip(p_local, p_empate, p_visitante):
        r = rng.choice([1, 0, -1], p=[pl, pd_, pa])
        resultados.append(r)

    df = pd.DataFrame({
        "gf_local_5j":      gf_h,
        "gc_local_5j":      gc_h,
        "gf_visitante_5j":  gf_a,
        "gc_visitante_5j":  gc_a,
        "forma_local_pts":  forma_h,
        "forma_visit_pts":  forma_a,
        "h2h_home_wins":    h2h_h,
        "h2h_draws":        h2h_d,
        "h2h_away_wins":    h2h_a,
        "diferencia_gf":    gf_h - gf_a,
        "diferencia_gc":    gc_h - gc_a,
        "factor_local":     np.ones(n),
        TARGET:             resultados,
    })
    return df


# ══════════════════════════════════════════
#  PREPROCESAMIENTO DE FEATURES
# ══════════════════════════════════════════

def _forma_a_pts(forma_str: str, n: int = 5) -> float:
    """Convierte 'WWDLW' en puntos: W=3, D=1, L=0."""
    if not isinstance(forma_str, str) or not forma_str:
        return 4.5  # valor neutro
    reciente = forma_str[-n:]
    pts = sum(3 if c == "W" else 1 if c == "D" else 0 for c in reciente)
    return round(pts / max(len(reciente), 1), 2)


def _h2h_parse(h2h_str: str) -> tuple[int, int, int]:
    """'H3-D2-A5' → (3, 2, 5)."""
    try:
        parts = h2h_str.split("-")
        h = int(parts[0][1:])
        d = int(parts[1][1:])
        a = int(parts[2][1:])
        return h, d, a
    except Exception:
        return 0, 0, 0


def preparar_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Recibe DataFrame con columnas raw del CSV y devuelve
    DataFrame con las columnas de FEATURES listas para el modelo.
    """
    out = df.copy()

    if "forma_local" in out.columns:
        out["forma_local_pts"]  = out["forma_local"].apply(_forma_a_pts)
    elif "forma_local_pts" not in out.columns:
        out["forma_local_pts"]  = 4.5

    if "forma_visitante" in out.columns:
        out["forma_visit_pts"]  = out["forma_visitante"].apply(_forma_a_pts)
    elif "forma_visit_pts" not in out.columns:
        out["forma_visit_pts"]  = 4.5

    if "h2h_historial" in out.columns:
        h2h_parsed = out["h2h_historial"].apply(_h2h_parse)
        out["h2h_home_wins"] = h2h_parsed.apply(lambda x: x[0])
        out["h2h_draws"]     = h2h_parsed.apply(lambda x: x[1])
        out["h2h_away_wins"] = h2h_parsed.apply(lambda x: x[2])
    else:
        out[["h2h_home_wins", "h2h_draws", "h2h_away_wins"]] = 0

    out["diferencia_gf"] = out["gf_local_5j"]   - out["gf_visitante_5j"]
    out["diferencia_gc"] = out["gc_local_5j"]    - out["gc_visitante_5j"]
    out["factor_local"]  = 1.0

    # Rellenar nulos numéricos con mediana
    for col in FEATURES:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(out[col].median() if col in out and out[col].notna().any() else 0.0)

    return out[FEATURES]


# ══════════════════════════════════════════
#  ENTRENAMIENTO
# ══════════════════════════════════════════

def entrenar_modelo(df: pd.DataFrame | None = None) -> dict:
    """
    Entrena Random Forest + Gradient Boosting y guarda el mejor modelo.
    Si no se provee df, usa datos históricos de CSV_HISTORICO o genera sintéticos.

    Retorna dict con métricas de rendimiento.
    """
    log.info("═" * 50)
    log.info("Iniciando entrenamiento del modelo")

    # ── Cargar datos ──────────────────────────────────────
    if df is None:
        if CSV_HISTORICO.exists():
            log.info(f"Cargando histórico desde {CSV_HISTORICO}")
            df = pd.read_csv(CSV_HISTORICO)
        else:
            log.warning("No hay datos históricos → usando datos sintéticos")
            df = _generar_datos_sinteticos(n=8000)

    # ── Feature engineering ───────────────────────────────
    X = preparar_features(df)

    if TARGET in df.columns:
        y = df[TARGET].astype(int)
    else:
        raise ValueError(f"Columna '{TARGET}' no encontrada en el dataset")

    log.info(f"Dataset: {len(X)} muestras | features: {len(FEATURES)}")
    log.info(f"Distribución: {y.value_counts().to_dict()}")

    # ── Split train/test ──────────────────────────────────
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )

    # ── Scaler ────────────────────────────────────────────
    scaler  = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    # ── Modelos candidatos ────────────────────────────────
    candidatos = {
        "RandomForest": RandomForestClassifier(
            n_estimators=300,
            max_depth=8,
            min_samples_leaf=10,
            class_weight="balanced",
            n_jobs=-1,
            random_state=42,
        ),
        "GradientBoosting": GradientBoostingClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        ),
    }

    metricas    = {}
    mejor_score = 0
    mejor_nombre = ""
    mejor_modelo = None

    for nombre, modelo in candidatos.items():
        log.info(f"Entrenando {nombre}…")
        modelo.fit(X_train_s, y_train)

        y_pred    = modelo.predict(X_test_s)
        acc       = accuracy_score(y_test, y_pred)
        cv_scores = cross_val_score(modelo, X_train_s, y_train, cv=5, scoring="accuracy")

        metricas[nombre] = {
            "accuracy":    round(acc, 4),
            "cv_mean":     round(cv_scores.mean(), 4),
            "cv_std":      round(cv_scores.std(),  4),
            "report":      classification_report(y_test, y_pred,
                               target_names=["Visitante","Empate","Local"],
                               labels=[-1, 0, 1]),
        }
        log.info(f"  Accuracy={acc:.3f} | CV={cv_scores.mean():.3f}±{cv_scores.std():.3f}")

        if cv_scores.mean() > mejor_score:
            mejor_score  = cv_scores.mean()
            mejor_nombre = nombre
            mejor_modelo = modelo

    # ── Feature importance (solo RF) ──────────────────────
    rf_model = candidatos.get("RandomForest")
    importances = {}
    if hasattr(rf_model, "feature_importances_"):
        importances = dict(zip(FEATURES, rf_model.feature_importances_.round(4)))
        sorted_imp  = sorted(importances.items(), key=lambda x: x[1], reverse=True)
        log.info("Feature importance (RF):")
        for feat, imp in sorted_imp:
            log.info(f"  {feat:25s}: {imp:.4f}")

    # ── Guardar modelo ganador + scaler ───────────────────
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH,  "wb") as f:
        pickle.dump(mejor_modelo, f)
    with open(SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)
    log.info(f"✓ Modelo '{mejor_nombre}' guardado en {MODEL_PATH}")

    # ── Reporte ───────────────────────────────────────────
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    reporte_txt = []
    reporte_txt.append(f"REPORTE DE RENDIMIENTO DEL MODELO — {pd.Timestamp.now()}\n")
    reporte_txt.append(f"Mejor modelo: {mejor_nombre} (CV accuracy={mejor_score:.4f})\n")
    reporte_txt.append(f"Muestras entrenamiento: {len(X_train)} | test: {len(X_test)}\n\n")

    for nombre, m in metricas.items():
        reporte_txt.append(f"{'─'*40}\n{nombre}\n{'─'*40}\n")
        reporte_txt.append(f"Accuracy: {m['accuracy']} | CV: {m['cv_mean']}±{m['cv_std']}\n")
        reporte_txt.append(m["report"] + "\n")

    if importances:
        reporte_txt.append(f"\nFEATURE IMPORTANCE (RandomForest):\n")
        for k, v in sorted(importances.items(), key=lambda x: x[1], reverse=True):
            reporte_txt.append(f"  {k:25s}: {v:.4f}\n")

    reporte_str = "".join(reporte_txt)
    REPORT_PATH.write_text(reporte_str, encoding="utf-8")
    log.info(f"✓ Reporte guardado en {REPORT_PATH}")

    return {
        "mejor_modelo":  mejor_nombre,
        "cv_accuracy":   mejor_score,
        "metricas":      metricas,
        "importances":   importances,
    }


# ══════════════════════════════════════════
#  PREDICCIÓN
# ══════════════════════════════════════════

def cargar_modelo() -> tuple:
    """Carga modelo y scaler desde disco."""
    if not MODEL_PATH.exists() or not SCALER_PATH.exists():
        raise FileNotFoundError(
            "Modelo no encontrado. Ejecuta primero: python main.py --mode train"
        )
    with open(MODEL_PATH,  "rb") as f:
        modelo = pickle.load(f)
    with open(SCALER_PATH, "rb") as f:
        scaler = pickle.load(f)
    return modelo, scaler


def predecir_partido(row: pd.Series | dict,
                     modelo=None, scaler=None) -> dict:
    """
    Predice el resultado de un partido.

    Parámetros:
        row: fila del DataFrame de partidos (o dict con los campos).
    Retorna:
        {
          "local_prob":    0.65,
          "draw_prob":     0.20,
          "away_prob":     0.15,
          "prediccion":    "Local",   # clase con mayor probabilidad
          "confidence":    0.65,      # prob de la clase predicha
        }
    """
    if modelo is None or scaler is None:
        modelo, scaler = cargar_modelo()

    df_row = pd.DataFrame([row])
    X      = preparar_features(df_row)
    X_s    = scaler.transform(X)

    probs  = modelo.predict_proba(X_s)[0]
    clases = list(modelo.classes_)

    prob_map = {c: p for c, p in zip(clases, probs)}
    p_local     = float(prob_map.get(1,  0.33))
    p_empate    = float(prob_map.get(0,  0.33))
    p_visitante = float(prob_map.get(-1, 0.34))

    prediccion_id = max(prob_map, key=prob_map.get)
    confidence    = float(prob_map[prediccion_id])
    prediccion    = LABEL_MAP[prediccion_id]

    return {
        "local_prob":    round(p_local, 4),
        "draw_prob":     round(p_empate, 4),
        "away_prob":     round(p_visitante, 4),
        "prediccion":    prediccion,
        "confidence":    round(confidence, 4),
    }


def predecir_todos(df_partidos: pd.DataFrame) -> pd.DataFrame:
    """Aplica predecir_partido a cada fila del DataFrame de partidos."""
    modelo, scaler = cargar_modelo()
    resultados = []
    for _, row in df_partidos.iterrows():
        pred = predecir_partido(row, modelo=modelo, scaler=scaler)
        resultados.append(pred)
    return pd.DataFrame(resultados)


# ══════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════

if __name__ == "__main__":
    log.info("Entrenando modelo con datos sintéticos (demo)…")
    metricas = entrenar_modelo()
    print(f"\nMejor modelo: {metricas['mejor_modelo']}")
    print(f"CV Accuracy : {metricas['cv_accuracy']:.3f}")
    print(f"\nReporte guardado en: {REPORT_PATH}")

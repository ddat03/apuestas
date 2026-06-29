# ============================================================
#  bet_tracker.py — Registro y seguimiento de apuestas
#
#  Base de datos SQLite local + estadísticas + gráficos Plotly
# ============================================================

import logging
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

from config import DB_APUESTAS, STATS_PATH, MONEDA, LOG_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("BetTracker")

# ─────────────────────────────────────────
#  ESQUEMA DE BASE DE DATOS
# ─────────────────────────────────────────
DB_URL = f"sqlite:///{DB_APUESTAS}"
engine = sa.create_engine(DB_URL, echo=False)
meta   = sa.MetaData()

apuestas_table = sa.Table(
    "apuestas", meta,
    sa.Column("id",              sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("fecha",           sa.Date),
    sa.Column("liga",            sa.String(50)),
    sa.Column("partido",         sa.String(120)),
    sa.Column("mi_prediccion",   sa.String(20)),   # Local/Empate/Visitante
    sa.Column("confianza",       sa.Float),
    sa.Column("cuota",           sa.Float),
    sa.Column("score",           sa.Float),
    sa.Column("apuesta_monto",   sa.Float),
    sa.Column("resultado",       sa.String(10)),   # WIN/LOSS/PUSH/PENDIENTE
    sa.Column("ganancia_perdida",sa.Float),
    sa.Column("notas",           sa.Text),
    sa.Column("creado_en",       sa.DateTime, default=datetime.utcnow),
)


def inicializar_db():
    """Crea la tabla si no existe."""
    DB_APUESTAS.parent.mkdir(parents=True, exist_ok=True)
    meta.create_all(engine)
    log.info(f"Base de datos inicializada: {DB_APUESTAS}")


# ══════════════════════════════════════════
#  CRUD
# ══════════════════════════════════════════

def registrar_apuesta(
    partido: str,
    liga: str,
    prediccion: str,
    confianza: float,
    cuota: float,
    monto: float,
    score: float = 0.0,
    resultado: str = "PENDIENTE",
    notas: str = "",
    fecha: str | None = None,
) -> int:
    """
    Registra una apuesta nueva.
    Retorna el ID de la apuesta insertada.
    """
    inicializar_db()
    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date() if fecha else datetime.utcnow().date()

    ganancia = 0.0
    if resultado == "WIN":
        ganancia = round(monto * (cuota - 1), 2)
    elif resultado == "LOSS":
        ganancia = round(-monto, 2)
    elif resultado == "PUSH":
        ganancia = 0.0

    with engine.connect() as conn:
        res = conn.execute(
            apuestas_table.insert().values(
                fecha=fecha_dt,
                liga=liga,
                partido=partido,
                mi_prediccion=prediccion,
                confianza=confianza,
                cuota=cuota,
                score=score,
                apuesta_monto=monto,
                resultado=resultado,
                ganancia_perdida=ganancia,
                notas=notas,
                creado_en=datetime.utcnow(),
            )
        )
        conn.commit()
        apuesta_id = res.inserted_primary_key[0]

    log.info(f"Apuesta registrada #{apuesta_id}: {partido} | {resultado} | "
             f"{'+' if ganancia >= 0 else ''}{ganancia:.2f} {MONEDA}")
    return apuesta_id


def actualizar_resultado(apuesta_id: int, resultado: str) -> bool:
    """
    Actualiza el resultado de una apuesta existente.
    resultado: 'WIN' | 'LOSS' | 'PUSH'
    """
    inicializar_db()
    with engine.connect() as conn:
        row = conn.execute(
            apuestas_table.select().where(apuestas_table.c.id == apuesta_id)
        ).fetchone()

    if not row:
        log.error(f"Apuesta #{apuesta_id} no encontrada")
        return False

    monto = row.apuesta_monto
    cuota = row.cuota
    if resultado == "WIN":
        ganancia = round(monto * (cuota - 1), 2)
    elif resultado == "LOSS":
        ganancia = round(-monto, 2)
    else:
        ganancia = 0.0

    with engine.connect() as conn:
        conn.execute(
            apuestas_table.update()
            .where(apuestas_table.c.id == apuesta_id)
            .values(resultado=resultado, ganancia_perdida=ganancia)
        )
        conn.commit()

    log.info(f"Apuesta #{apuesta_id} actualizada → {resultado} | {ganancia:+.2f} {MONEDA}")
    return True


def listar_apuestas(dias: int = 30) -> pd.DataFrame:
    """Devuelve apuestas de los últimos N días como DataFrame."""
    inicializar_db()
    desde = (datetime.utcnow().date() - timedelta(days=dias))
    with engine.connect() as conn:
        rows = conn.execute(
            apuestas_table.select()
            .where(apuestas_table.c.fecha >= desde)
            .order_by(apuestas_table.c.fecha.desc())
        ).fetchall()
    return pd.DataFrame(rows, columns=apuestas_table.columns.keys())


# ══════════════════════════════════════════
#  ESTADÍSTICAS
# ══════════════════════════════════════════

def _df_completo() -> pd.DataFrame:
    inicializar_db()
    with engine.connect() as conn:
        rows = conn.execute(apuestas_table.select()).fetchall()
    df = pd.DataFrame(rows, columns=apuestas_table.columns.keys())
    # Solo apuestas resueltas
    return df[df["resultado"].isin(["WIN", "LOSS", "PUSH"])].copy()


def calcular_roi(df: pd.DataFrame) -> dict:
    """Calcula métricas de ROI para el DataFrame dado."""
    if df.empty:
        return {"apostado": 0, "ganado": 0, "roi_pct": 0,
                "winrate": 0, "n_apuestas": 0}
    apostado = df["apuesta_monto"].sum()
    ganado   = df["ganancia_perdida"].sum()
    roi      = (ganado / apostado * 100) if apostado else 0
    wins     = (df["resultado"] == "WIN").sum()
    wrate    = (wins / len(df) * 100) if len(df) else 0
    return {
        "apostado":    round(float(apostado), 2),
        "ganado":      round(float(ganado), 2),
        "roi_pct":     round(float(roi), 2),
        "winrate":     round(float(wrate), 2),
        "n_apuestas":  int(len(df)),
        "cuota_prom":  round(float(df["cuota"].mean()), 2) if "cuota" in df else 0,
    }


def estadisticas_generales() -> dict:
    """Panel completo de estadísticas."""
    df = _df_completo()
    stats = {
        "total":        calcular_roi(df),
        "por_liga":     {},
        "por_cuota":    {},
        "por_semana":   {},
    }

    # Por liga
    for liga, grp in df.groupby("liga"):
        stats["por_liga"][liga] = calcular_roi(grp)

    # Por rango de cuota
    bins   = [1.0, 1.5, 2.0, 2.5, 3.0, 99.0]
    labels = ["1.0-1.5", "1.5-2.0", "2.0-2.5", "2.5-3.0", "3.0+"]
    if not df.empty:
        df["rango_cuota"] = pd.cut(df["cuota"], bins=bins, labels=labels)
        for rango, grp in df.groupby("rango_cuota", observed=True):
            stats["por_cuota"][str(rango)] = calcular_roi(grp)

    # Por semana
    if not df.empty:
        df["semana"] = pd.to_datetime(df["fecha"]).dt.to_period("W").astype(str)
        for semana, grp in df.groupby("semana"):
            stats["por_semana"][semana] = calcular_roi(grp)

    # Guardar JSON
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"✓ Estadísticas guardadas en {STATS_PATH}")
    return stats


def imprimir_dashboard():
    """Imprime un resumen bonito en terminal."""
    stats = estadisticas_generales()
    t = stats["total"]
    sep = "─" * 45

    print(f"\n{'═'*45}")
    print(f"  DASHBOARD DE APUESTAS")
    print(f"{'═'*45}")
    print(f"  Total apostado : {t['apostado']:>8.2f} {MONEDA}")
    print(f"  Ganancia/Pérd  : {t['ganado']:>+8.2f} {MONEDA}")
    print(f"  ROI            : {t['roi_pct']:>+7.2f}%")
    print(f"  Win rate       : {t['winrate']:>7.2f}%")
    print(f"  Nº apuestas    : {t['n_apuestas']:>8}")
    print(f"  Cuota promedio : {t['cuota_prom']:>8.2f}")

    if stats["por_liga"]:
        print(f"\n{sep}")
        print("  POR LIGA")
        print(sep)
        for liga, m in sorted(stats["por_liga"].items(),
                               key=lambda x: x[1]["roi_pct"], reverse=True):
            print(f"  {liga:<25} ROI={m['roi_pct']:>+6.2f}%  WR={m['winrate']:>5.1f}%  N={m['n_apuestas']}")

    if stats["por_cuota"]:
        print(f"\n{sep}")
        print("  POR RANGO DE CUOTA")
        print(sep)
        for rango, m in stats["por_cuota"].items():
            print(f"  {rango:<12} ROI={m['roi_pct']:>+6.2f}%  WR={m['winrate']:>5.1f}%  N={m['n_apuestas']}")

    print(f"{'═'*45}\n")


# ══════════════════════════════════════════
#  GRÁFICOS PLOTLY
# ══════════════════════════════════════════

def generar_graficos(output_dir: Path | None = None):
    """
    Genera gráficos interactivos con Plotly y los guarda como HTML.
    output_dir: directorio donde guardar (por defecto reports/)
    """
    try:
        import plotly.graph_objects as go
        import plotly.express as px
    except ImportError:
        log.warning("Plotly no instalado. Instala con: pip install plotly")
        return

    from config import REPORTS_DIR
    out = output_dir or REPORTS_DIR
    out.mkdir(parents=True, exist_ok=True)

    df = _df_completo()
    if df.empty:
        log.warning("Sin datos suficientes para gráficos")
        return

    df["fecha"]   = pd.to_datetime(df["fecha"])
    df_sorted     = df.sort_values("fecha")

    # ── 1. Ganancias acumuladas ───────────────────────────
    df_sorted["acumulado"] = df_sorted["ganancia_perdida"].cumsum()
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=df_sorted["fecha"], y=df_sorted["acumulado"],
        mode="lines+markers",
        line=dict(color="#2196F3", width=2),
        marker=dict(size=6),
        fill="tozeroy",
        fillcolor="rgba(33,150,243,0.1)",
        name="P&L acumulado",
    ))
    fig1.add_hline(y=0, line_dash="dash", line_color="gray")
    fig1.update_layout(
        title="Ganancia/Pérdida Acumulada",
        xaxis_title="Fecha", yaxis_title=f"P&L ({MONEDA})",
        template="plotly_dark",
    )
    fig1.write_html(out / "grafico_pnl.html")

    # ── 2. W/L por semana ────────────────────────────────
    df_sorted["semana"] = df_sorted["fecha"].dt.to_period("W").dt.start_time
    semanal = df_sorted.groupby(["semana","resultado"]).size().unstack(fill_value=0)
    fig2 = go.Figure()
    for resultado, color in [("WIN","#4CAF50"), ("LOSS","#f44336"), ("PUSH","#9E9E9E")]:
        if resultado in semanal.columns:
            fig2.add_trace(go.Bar(
                name=resultado,
                x=semanal.index.astype(str),
                y=semanal[resultado],
                marker_color=color,
            ))
    fig2.update_layout(
        barmode="group",
        title="Resultados Semanales",
        xaxis_title="Semana", yaxis_title="Cantidad",
        template="plotly_dark",
    )
    fig2.write_html(out / "grafico_semanal.html")

    # ── 3. Distribución por liga ─────────────────────────
    liga_count = df.groupby("liga").size().reset_index(name="count")
    fig3 = px.pie(liga_count, names="liga", values="count",
                  title="Apuestas por Liga", template="plotly_dark")
    fig3.write_html(out / "grafico_ligas.html")

    # ── 4. ROI por liga ──────────────────────────────────
    roi_liga = df.groupby("liga").apply(
        lambda g: pd.Series({
            "roi": ((g["ganancia_perdida"].sum() / g["apuesta_monto"].sum()) * 100)
                    if g["apuesta_monto"].sum() else 0,
            "n":   len(g),
        }),
        include_groups=False,
    ).reset_index()
    fig4 = px.bar(roi_liga, x="liga", y="roi", text="n",
                  color="roi", color_continuous_scale="RdYlGn",
                  title="ROI por Liga (%)", template="plotly_dark")
    fig4.update_traces(texttemplate="%{text} apuestas", textposition="outside")
    fig4.add_hline(y=0, line_dash="dash", line_color="white")
    fig4.write_html(out / "grafico_roi_liga.html")

    log.info(f"✓ 4 gráficos guardados en {out}")
    print(f"\nGráficos disponibles en: {out}")
    for g in ["grafico_pnl.html", "grafico_semanal.html",
              "grafico_ligas.html", "grafico_roi_liga.html"]:
        print(f"  → {out / g}")


# ══════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════

if __name__ == "__main__":
    inicializar_db()

    # ── Insertar apuestas de ejemplo ──────────────────────
    print("Insertando apuestas de ejemplo…")
    ids = []
    ejemplos = [
        ("Manchester City vs Arsenal",  "Premier League", "Local",    0.68, 1.75, 25.0, 42.0, "WIN"),
        ("Real Madrid vs Barcelona",     "La Liga",        "Local",    0.72, 1.60, 20.0, 38.0, "LOSS"),
        ("Inter vs Juventus",            "Serie A",        "Empate",   0.62, 3.20, 15.0, 18.0, "WIN"),
        ("Bayern vs Dortmund",           "Bundesliga",     "Local",    0.70, 1.55, 30.0, 45.0, "WIN"),
        ("PSG vs Marseille",             "Ligue 1",        "Local",    0.75, 1.45, 20.0, 35.0, "LOSS"),
    ]
    for partido, liga, pred, conf, cuota, monto, score, resultado in ejemplos:
        aid = registrar_apuesta(partido, liga, pred, conf, cuota, monto, score, resultado)
        ids.append(aid)

    print(f"✓ {len(ids)} apuestas registradas")
    imprimir_dashboard()

    print("Generando gráficos…")
    generar_graficos()

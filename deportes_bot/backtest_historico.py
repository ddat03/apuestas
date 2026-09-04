# ============================================================
#  backtest_historico.py — Walk-forward honesto del método
#  sharp_ev (ancla Pinnacle) sobre datos reales de football-data.co.uk
#
#  Por qué esta fuente y no The Odds API: el historical endpoint
#  de The Odds API devuelve 401 HISTORICAL_UNAVAILABLE_ON_FREE_
#  USAGE_PLAN en el plan gratuito (confirmado en vivo). football-
#  data.co.uk publica, por partido, cuotas de Pinnacle de apertura
#  (PSH/PSD/PSA) Y DE CIERRE (PSCH/PSCD/PSCA) reales, más el máximo
#  entre las casas que rastrean (MaxH/MaxD/MaxA) — exactamente lo
#  que hace falta para replicar sharp_ev.py sin inventar nada:
#
#    fair_open  = no-vig de Pinnacle apertura   (= "ancla al pick")
#    best_soft  = Max{H,D,A}                    (= "mejor casa" al pick)
#    fair_close = no-vig de Pinnacle cierre      (= verdad de cierre)
#    resultado  = FTR real                       (= liquidación real)
#
#  No hay ningún modelo entrenado (sharp_ev es una regla fija de
#  banda de EV, sin parámetros ajustados), así que "walk-forward"
#  aquí se reduce a aplicar la regla en orden cronológico —no hay
#  fuga de datos posible porque apertura y cierre son snapshots de
#  mercado reales en momentos distintos, ambos anteriores al
#  resultado. Igual se reporta la partición temporal (mitad 1 vs
#  mitad 2) para ver si el efecto es estable en el tiempo.
# ============================================================

import numpy as np
import pandas as pd
import requests

EV_MIN, EV_MAX = 0.025, 0.10   # mismos umbrales que sharp_live.py
LIGAS = {"E0": "EPL", "SP1": "LaLiga", "I1": "SerieA", "D1": "Bundesliga", "F1": "Ligue1"}
TEMPORADAS = ["2021", "2122", "2223", "2324", "2425", "2526"]
COLS = ["Date", "HomeTeam", "AwayTeam", "FTR", "PSH", "PSD", "PSA",
        "PSCH", "PSCD", "PSCA", "MaxH", "MaxD", "MaxA"]


def descargar_todo() -> pd.DataFrame:
    partes = []
    for temp in TEMPORADAS:
        for code, nombre in LIGAS.items():
            url = f"https://www.football-data.co.uk/mmz4281/{temp}/{code}.csv"
            try:
                df = pd.read_csv(url)
            except Exception as e:
                print(f"  {temp} {nombre}: error descarga ({e})")
                continue
            if not all(c in df.columns for c in COLS):
                print(f"  {temp} {nombre}: sin columnas Pinnacle completas, se omite")
                continue
            df = df[COLS].copy()
            df["liga"], df["temporada"] = nombre, temp
            partes.append(df)
    df = pd.concat(partes, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    antes = len(df)
    df = df.dropna(subset=COLS)
    print(f"\nPartidos descargados: {antes} | con cuotas Pinnacle open+close completas: {len(df)}")
    return df.sort_values("Date").reset_index(drop=True)


def _novig(o: dict) -> dict:
    inv = {k: 1 / v for k, v in o.items()}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def _novig_shin(o: dict) -> dict:
    """De-vig de Shin (1993): corrige el sesgo favorito-longshot que el
    de-vig proporcional NO corrige (el proporcional sobreestima la
    probabilidad real de empates/longshots — ver docstring del módulo).
    Resuelve por bisección la fracción z de 'dinero informado' tal que
    las p_i sumen 1."""
    ks = list(o.keys())
    pi = np.array([1 / o[k] for k in ks])

    def p_of_z(z):
        if z <= 1e-9:
            return pi.copy()
        return (np.sqrt(z ** 2 + 4 * (1 - z) * pi ** 2) - z) / (2 * (1 - z))

    lo, hi = 0.0, 0.4999
    for _ in range(60):
        mid = (lo + hi) / 2
        if p_of_z(mid).sum() > 1:
            lo = mid
        else:
            hi = mid
    p = p_of_z((lo + hi) / 2)
    p = p / p.sum()          # limpia el residuo numérico de la bisección
    return dict(zip(ks, p))


def generar_picks(df: pd.DataFrame, devig=_novig) -> pd.DataFrame:
    """Aplica EXACTAMENTE la regla de sharp_ev.analizar_evento: ancla
    Pinnacle no-vig al momento del pick vs mejor cuota disponible,
    banda de cordura EV_MIN-EV_MAX. Sin mirar PSC (cierre) ni FTR
    para decidir — eso solo se usa después, para medir CLV y resultado.

    `devig` es intercambiable: _novig (proporcional, el que usa
    sharp_ev.py hoy) o _novig_shin (corrige favorito-longshot) — para
    comprobar si el edge medido depende del método de de-vig."""
    filas = []
    for r in df.itertuples(index=False):
        fair_open = devig({"H": r.PSH, "D": r.PSD, "A": r.PSA})
        fair_close = devig({"H": r.PSCH, "D": r.PSCD, "A": r.PSCA})
        best = {"H": r.MaxH, "D": r.MaxD, "A": r.MaxA}
        for oc in ("H", "D", "A"):
            ev = fair_open[oc] * best[oc] - 1.0
            if not (EV_MIN <= ev <= EV_MAX):
                continue
            clv_ev = fair_close[oc] * best[oc] - 1.0     # ev_vs_cierre (sharp_ev.ev_vs_cierre)
            gano = r.FTR == oc
            filas.append({
                "fecha": r.Date, "liga": r.liga, "temporada": r.temporada,
                "partido": f"{r.HomeTeam} vs {r.AwayTeam}", "outcome": oc,
                "odds_taken": best[oc], "fair_open": fair_open[oc], "ev_pick": ev,
                "odds_close": {"H": r.PSCH, "D": r.PSCD, "A": r.PSCA}[oc],
                "fair_close": fair_close[oc], "clv_ev": clv_ev,
                "beat_close": best[oc] > {"H": r.PSCH, "D": r.PSCD, "A": r.PSCA}[oc],
                "result": "WIN" if gano else "LOSS",
                "pnl_u": (best[oc] - 1.0) if gano else -1.0,
            })
    return pd.DataFrame(filas)


def _ic_media(x: np.ndarray, reps: int = 10_000, seed: int = 42) -> tuple[float, float, float]:
    """Media + IC 95% por bootstrap (más honesto que normal-approx con
    colas pesadas de cuotas altas). Devuelve (media, ic_bajo, ic_alto)."""
    rng = np.random.default_rng(seed)
    n = len(x)
    medias_boot = rng.choice(x, size=(reps, n), replace=True).mean(axis=1)
    return float(x.mean()), float(np.percentile(medias_boot, 2.5)), float(np.percentile(medias_boot, 97.5))


def _t_test_vs_cero(x: np.ndarray) -> tuple[float, float]:
    """t-stat y p-valor de una muestra contra H0: media=0 (aprox normal,
    válido con n grande — aquí se usan cientos/miles de picks)."""
    n = len(x)
    t = x.mean() / (x.std(ddof=1) / np.sqrt(n))
    # p-valor de dos colas vía aproximación normal de la distribución t
    from math import erf, sqrt
    p = 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))
    return float(t), float(p)


def reportar(picks: pd.DataFrame, etiqueta: str = "TOTAL") -> None:
    n = len(picks)
    if n == 0:
        print(f"  [{etiqueta}] 0 picks — nada que reportar")
        return
    wr = (picks["result"] == "WIN").mean()
    pnl = picks["pnl_u"].to_numpy()
    clv = picks["clv_ev"].to_numpy()
    roi_m, roi_lo, roi_hi = _ic_media(pnl)
    clv_m, clv_lo, clv_hi = _ic_media(clv)
    t, p = _t_test_vs_cero(clv)
    print(f"\n  ── {etiqueta} — n={n} ──")
    print(f"  Win rate           : {wr:.1%}")
    print(f"  ROI (u/pick)       : {roi_m:+.3f}  IC95% [{roi_lo:+.3f}, {roi_hi:+.3f}]"
          f"  {'← incluye 0' if roi_lo <= 0 <= roi_hi else '← NO incluye 0'}")
    print(f"  CLV EV medio       : {clv_m:+.3%}  IC95% [{clv_lo:+.3%}, {clv_hi:+.3%}]"
          f"  {'← incluye 0' if clv_lo <= 0 <= clv_hi else '← NO incluye 0'}")
    print(f"  batió cierre       : {picks['beat_close'].mean():.1%}")
    print(f"  t-test CLV vs 0    : t={t:+.2f}  p={p:.4f}"
          f"  {'(no se puede rechazar H0: CLV=0)' if p >= 0.05 else '(CLV ≠ 0 con 95% confianza)'}")


def main() -> None:
    print("Descargando histórico de football-data.co.uk (5 ligas × 6 temporadas)…")
    df = descargar_todo()

    print(f"\n{'═'*64}\n  ROBUSTEZ: ¿el edge depende del método de de-vig?\n{'═'*64}")
    print("  (proporcional = el que usa sharp_ev.py hoy; Shin corrige el "
          "sesgo favorito-longshot que el proporcional NO corrige)")
    picks_prop = generar_picks(df, _novig)
    reportar(picks_prop, f"DE-VIG PROPORCIONAL (actual) — n={len(picks_prop)}")
    picks_shin = generar_picks(df, _novig_shin)
    reportar(picks_shin, f"DE-VIG SHIN (corregido) — n={len(picks_shin)}")

    print(f"\n{'═'*64}\n  BACKTEST WALK-FORWARD — método sharp_ev, datos reales\n{'═'*64}")
    print(f"  Periodo: {df['Date'].min().date()} → {df['Date'].max().date()}")
    print(f"  Partidos evaluados: {len(df)} | picks generados (EV {EV_MIN:.1%}-{EV_MAX:.1%}, de-vig proporcional): {len(picks_prop)}")

    picks = picks_prop
    reportar(picks, "TOTAL")

    # Partición temporal (walk-forward): ¿el efecto es estable en el tiempo?
    corte = picks["fecha"].median()
    reportar(picks[picks["fecha"] < corte], f"MITAD 1 (hasta {corte.date()})")
    reportar(picks[picks["fecha"] >= corte], f"MITAD 2 (desde {corte.date()})")

    # Por outcome (¿el patrón favorito-longshot que vimos en vivo se repite?)
    print(f"\n  Distribución de picks por outcome:")
    print(picks["outcome"].value_counts().rename({"H": "local", "D": "empate", "A": "visitante"}).to_string())
    for oc, nombre in [("H", "LOCAL"), ("D", "EMPATE"), ("A", "VISITANTE")]:
        reportar(picks[picks["outcome"] == oc], nombre)

    out = "reports/backtest_historico.csv"
    import os
    os.makedirs("reports", exist_ok=True)
    picks.to_csv(out, index=False)
    print(f"\n  Detalle de los {len(picks)} picks guardado en {out}")


if __name__ == "__main__":
    main()

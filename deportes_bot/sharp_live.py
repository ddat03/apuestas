# ============================================================
#  sharp_live.py — Test forward del sistema +EV con ancla Pinnacle
#
#  Creado por Diego Aleman.
#
#  Cada ejecución (1 llamada de cuotas por liga, nada más):
#    1. Baja cuotas h2h + totals (goles) de las ligas configuradas
#       (The Odds API).
#    2. Con esa MISMA respuesta:
#       a. busca valor 1X2 (mejor casa reputada vs Pinnacle no-vig)
#          y registra los picks NUEVOS en data/clv.db;
#       b. busca patas "seguras" (prob. justa Pinnacle ≥ 70%) en
#          1X2 (combo_builder.py) Y en goles Over/Under
#          (totals_ev.py) — cero llamadas extra a la API — y arma
#          combinadas de 2-3 patas de ligas distintas;
#       c. si hay API_FOOTBALL_KEY, evalúa cada pata segura contra
#          forma reciente + H2H (stats_confluence.py) y descarta la
#          que la estadística contradiga claramente;
#       d. a los picks pendientes cuyo partido está por empezar,
#          les guarda la línea de CIERRE de Pinnacle → CLV.
#    3. Si hay picks (o patas de combinadas) cuyo partido ya
#       terminó, pide el marcador (endpoint scores), los liquida
#       → ROI de papel, y liquida las combinadas cuyas patas ya
#       estén todas resueltas.
#    4. Resumen por consola + Telegram (+ aviso si queda poca cuota).
#
#  NO coloca ninguna apuesta real. Mide el CLV (picks individuales)
#  y el acierto real vs. la probabilidad prometida (combinadas)
#  durante 3-4 semanas: si son claramente positivos, el método
#  tiene edge y se puede pensar en escalar. Si no, es ruido.
#
#  Consumo API: ver el comentario junto a MARKETS/QUOTA_ALERTA_BAJO
#  más abajo — con el mercado de goles sumado, el costo real es el
#  doble de lo que un comentario viejo acá asumía.
#
#  Uso:
#    python sharp_live.py            → un ciclo
#    python sharp_live.py --loop     → repite cada SLEEP_HORAS
#    python sharp_live.py --resumen  → estado de clv.db y sale
#    python sharp_live.py --picks    → lista picks individuales
#    python sharp_live.py --combos   → lista combinadas seguras
# ============================================================

import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import clv_db
import combo_builder
import sharp_ev
import totals_ev
from config import (API_FOOTBALL_KEY, THE_ODDS_API_BASE, THE_ODDS_API_KEY,
                    TELEGRAM_CHAT_ID, TELEGRAM_TOKEN)

# ── Parámetros del test ─────────────────────────────────────────────────
LIGAS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
    "soccer_germany_bundesliga", "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
]
REGIONS = "eu,uk"
MARKETS = "h2h,totals"
# OJO con la cuota: The Odds API cobra créditos = nº regiones × nº mercados
# POR REQUEST (no 1 por request como asumía un comentario viejo acá). Con
# 2 regiones × 2 mercados = 4 créditos × 6 ligas × 1 corrida/día × 30 días
# ≈ 720/mes — ya se come casi todo el plan free (500/mes) con margen para
# los pedidos de /scores. Por eso GitHub Actions corre 1 vez/día, no 2
# (ver .github/workflows/sharp_live.yml) — con 2/día esto sería ~1440/mes.
QUOTA_ALERTA_BAJO = 60   # si "requests-remaining" baja de esto, avisar por Telegram
EV_MIN = 0.025         # 2.5% de valor mínimo contra Pinnacle no-vig
EV_MAX = 0.10          # por encima de esto casi siempre es cuota stale / mal emparejada
MIN_BOOKS = 10         # mínimo de casas reputadas en el evento
STAKE_U = 1.0          # unidades por pick (plano — Kelly viene después si hay edge)

# ── Parámetros de combinadas seguras (combo_builder.py) ───────────────
# Usan los MISMOS datos ya bajados arriba — cero llamadas extra a la API.
PATA_PROB_MIN         = 0.70   # prob. justa mínima (Pinnacle) para que una pata cuente como "segura"
PATA_MAX_DESCUENTO    = 0.08   # cuánto margen de más se tolera pagar por esa seguridad
COMBOS_TAMANOS        = (2, 3)
COMBOS_MAX_POR_TAMANO = 2
DESCUENTO_CORRELACION = 0.04
STAKE_COMBO_U         = 1.0
USAR_CONFLUENCIA_STATS = True   # ver stats_confluence.py — requiere API_FOOTBALL_KEY
# Ventana antes del kickoff en la que se (re)captura la línea de Pinnacle.
# Se guarda en cada ciclo, así que el último snapshot antes del saque es el
# que queda como "cierre". Amplia (14 h) para no perderla si el bot corre
# solo 2 veces al día.
CAPTURAR_CIERRE_HORAS = 14
SLEEP_HORAS = 12

# Casas grises / no accesibles desde LatAm de forma fiable — se ignoran
# tanto para colocar el pick como para el conteo de MIN_BOOKS.
# "onexbet" (1xBet) se sacó de esta lista: Diego apuesta ahí realmente
# (1xbet.ec), así que excluirla solo escondía la casa que sí puede usar.
BOOKS_EXCLUIDOS = {
    "mybookieag", "betonlineag", "betanysports", "gtbets",
    "everygame", "sport888", "betfair_sb_uk",
}


def _tg(texto: str) -> None:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"},
            timeout=10,
        )
    except requests.RequestException:
        pass


def _get(path: str, **params) -> tuple[list, str]:
    params["apiKey"] = THE_ODDS_API_KEY
    r = requests.get(f"{THE_ODDS_API_BASE}/{path}", params=params, timeout=25)
    r.raise_for_status()
    return r.json(), r.headers.get("x-requests-remaining", "?")


def _limpiar_books(event: dict) -> dict:
    """Quita del evento las casas excluidas (grises / no accesibles)."""
    event = dict(event)
    event["bookmakers"] = [
        b for b in event.get("bookmakers", []) if b["key"] not in BOOKS_EXCLUIDOS
    ]
    return event


def _fmt_outcome(p_outcome: str, home: str, away: str) -> str:
    mapa = {"local": home, "empate": "Empate", "visitante": away}
    if p_outcome in mapa:
        return mapa[p_outcome]
    if p_outcome.startswith("over_"):
        return f"Más de {p_outcome.split('_', 1)[1]} goles"
    if p_outcome.startswith("under_"):
        return f"Menos de {p_outcome.split('_', 1)[1]} goles"
    return p_outcome


# ── ciclo ─────────────────────────────────────────────────────────────

def ciclo() -> None:
    print(f"\n[{datetime.now(timezone.utc):%Y-%m-%d %H:%M}] ── ciclo sharp_live ──")
    ahora = datetime.now(timezone.utc)
    pend = {(r["event_id"], r["outcome"]): r for r in clv_db.pendientes()}
    ligas_con_pend_terminados: set[str] = set()

    nuevos, cierres = [], 0
    patas_pool: list = []   # PataSegura (h2h) y/o PataTotals (goles) — mismos campos clave
    quota_restante_min: int | None = None

    for liga in LIGAS:
        try:
            eventos, rem = _get(f"sports/{liga}/odds", regions=REGIONS,
                                markets=MARKETS, oddsFormat="decimal")
        except requests.RequestException as e:
            print(f"  {liga}: error cuotas ({e})")
            continue
        print(f"  {liga}: {len(eventos)} eventos | quota restante {rem}")
        try:
            rem_i = int(rem)
            quota_restante_min = rem_i if quota_restante_min is None else min(quota_restante_min, rem_i)
        except ValueError:
            pass

        for ev in eventos:
            ev["sport_key"] = liga
            ev = _limpiar_books(ev)
            ini = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))

            # a. picks nuevos (solo partidos que no empezaron)
            if ini > ahora:
                for pick in sharp_ev.analizar_evento(ev, EV_MIN, EV_MAX, MIN_BOOKS):
                    if clv_db.registrar_pick(pick, STAKE_U):
                        nuevos.append(pick)

                pata = combo_builder.pata_segura_evento(
                    ev, PATA_PROB_MIN, MIN_BOOKS, PATA_MAX_DESCUENTO)
                if pata:
                    patas_pool.append(pata)

                pata_gol = totals_ev.pata_segura_evento(
                    ev, PATA_PROB_MIN, MIN_BOOKS, PATA_MAX_DESCUENTO)
                if pata_gol:
                    patas_pool.append(pata_gol)

            # b. línea de cierre para picks pendientes de este evento,
            #    en la ventana previa al kickoff o ya empezados
            if ini <= ahora + timedelta(hours=CAPTURAR_CIERRE_HORAS):
                precios = sharp_ev._precios_por_casa(ev)
                if sharp_ev.ANCLA in precios:
                    fair = sharp_ev._novig(precios[sharp_ev.ANCLA])
                    for (eid, outcome), row in pend.items():
                        if eid != ev.get("id"):
                            continue
                        oc = precios[sharp_ev.ANCLA].get(outcome)
                        if not oc:
                            continue
                        clv = sharp_ev.ev_vs_cierre(row["odds_taken"], fair[outcome])
                        clv_db.actualizar_cierre(eid, outcome, oc, fair[outcome],
                                                 clv, row["odds_taken"])
                        cierres += 1

        # ¿hay picks pendientes de esta liga cuyo partido ya terminó?
        for (eid, oc), row in pend.items():
            if row["sport"] != liga:
                continue
            t = datetime.fromisoformat(row["commence_time"].replace("Z", "+00:00"))
            if t < ahora - timedelta(hours=2.5):
                ligas_con_pend_terminados.add(liga)

    # ── confluencia estadística (forma + H2H, API-Football) ──────────
    # Segunda mirada independiente del precio: si la forma/H2H CONTRADICE
    # claramente al favorito del mercado, esa pata se descarta de las
    # combinadas (nunca al revés — sin datos no descarta nada).
    if USAR_CONFLUENCIA_STATS and API_FOOTBALL_KEY and patas_pool:
        import stats_confluence
        from data_collector import _descargar_ventana_fechas
        try:
            fixtures_cache = _descargar_ventana_fechas(dias_atras=5, dias_adelante=2)
        except Exception as e:
            print(f"  confluencia: error bajando fixtures API-Football ({e})")
            fixtures_cache = []

        patas_confirmadas = []
        for pata in patas_pool:
            c = stats_confluence.evaluar(pata, fixtures_cache)
            if c.estado == "contradice":
                print(f"  ✗ pata descartada por confluencia: {pata.home} vs {pata.away} "
                      f"({pata.outcome}) — {c.detalle}")
            else:
                patas_confirmadas.append(pata)
        patas_pool = patas_confirmadas

    # ── menú de TODAS las patas seguras del ciclo ──────────────────────
    # No solo las que terminan empaquetadas en un combo automático: Diego
    # prefiere ver el menú completo y elegir él mismo qué pata de qué
    # partido combinar, en vez de aceptar un paquete ya armado (misma
    # lógica por la que no va a tomar la "combinada del día" de 1xbet
    # tal cual — quiere ver las patas sueltas y armar la suya).
    patas_nuevas = [p for p in patas_pool
                    if clv_db.registrar_pick(p, STAKE_COMBO_U, origen="pata_segura")]

    # ── combinadas sugeridas (automáticas, mismos datos, sin costo extra) ──
    # Se siguen armando y registrando para medir calibración (resumen_combos:
    # ¿el acierto real se parece a la prob. prometida?) — no son una
    # recomendación de "apostá esto tal cual".
    combos_nuevos: list[combo_builder.ComboPropuesto] = []
    if len(patas_pool) >= 2:
        for combo in combo_builder.armar_combos(
                patas_pool, COMBOS_TAMANOS, COMBOS_MAX_POR_TAMANO, DESCUENTO_CORRELACION):
            pick_ids = [clv_db.pick_id(p.event_id, p.outcome) for p in combo.patas]
            if clv_db.combo_existe(pick_ids):
                continue
            clv_db.crear_combo(combo.tipo, pick_ids, combo.cuota_total,
                               combo.prob_producto, combo.prob_ajustada, STAKE_COMBO_U)
            combos_nuevos.append(combo)

    # ── liquidar partidos terminados ──
    liquidados = 0
    for liga in ligas_con_pend_terminados:
        try:
            scores, _ = _get(f"sports/{liga}/scores", daysFrom=2)
        except requests.RequestException:
            continue
        por_id = {s["id"]: s for s in scores}
        for (eid, outcome), row in pend.items():
            if row["sport"] != liga:
                continue
            s = por_id.get(eid)
            if not s or not s.get("completed") or not s.get("scores"):
                continue
            marcador = {x["name"]: int(x["score"]) for x in s["scores"]}
            gh, ga = marcador.get(s["home_team"]), marcador.get(s["away_team"])
            if gh is None or ga is None:
                continue

            if outcome in ("local", "empate", "visitante"):
                gano = "local" if gh > ga else "visitante" if ga > gh else "empate"
                resultado = "WIN" if outcome == gano else "LOSS"
            elif outcome.startswith("over_") or outcome.startswith("under_"):
                resultado = totals_ev.liquidar_outcome(outcome, gh, ga)
            else:
                continue

            clv_db.liquidar(eid, outcome, resultado)
            liquidados += 1

    combos_liquidados = clv_db.liquidar_combos()

    # ── avisos + resumen ──
    for p in nuevos:
        msg = (
            f"🎯 <b>[SHARP] Pick</b>\n{p.home} vs {p.away}\n"
            f"<b>{_fmt_outcome(p.outcome, p.home, p.away)}</b> @ {p.best_odds} ({p.best_book})\n"
            f"Pinnacle no-vig {p.fair_prob:.1%} · EV +{p.ev:.1%} · {p.n_books} casas\n"
            f"{p.commence_time[:16].replace('T', ' ')}"
        )
        print("  + " + msg.replace("\n", " | "))
        _tg(msg)

    if patas_nuevas:
        lineas = [
            "🗂️ <b>[SHARP] Menú de patas seguras</b>",
            "Elegí vos qué combinar — no hace falta aceptar un paquete armado:",
            "",
        ]
        for p in sorted(patas_nuevas, key=lambda p: p.fair_prob, reverse=True):
            lineas.append(
                f"• <b>{_fmt_outcome(p.outcome, p.home, p.away)}</b> "
                f"({p.home} vs {p.away}) @ {p.best_odds} ({p.best_book})\n"
                f"   Pinnacle {p.fair_prob:.0%} · {p.sport} · "
                f"{p.commence_time[:16].replace('T', ' ')}"
            )
        msg = "\n".join(lineas)
        print("  📋 " + msg.replace("\n", " | "))
        _tg(msg)

    for c in combos_nuevos:
        patas_txt = "\n".join(
            f"  + {_fmt_outcome(p.outcome, p.home, p.away)} ({p.home} vs {p.away}) "
            f"@ {p.best_odds} · Pinnacle {p.fair_prob:.0%}"
            for p in c.patas
        )
        msg = (
            f"🛡️ <b>[SHARP] Sugerencia automática ({c.tipo})</b>\n{patas_txt}\n"
            f"Cuota total <b>{c.cuota_total}</b> · Prob. ajustada <b>{c.prob_ajustada:.0%}</b> "
            f"(cruda {c.prob_producto:.0%}) · EV {c.ev_combo:+.1%}\n"
            f"<i>Es un paquete armado por reglas fijas, solo para medir calibración — "
            f"no hace falta tomarlo tal cual, mirá el menú de arriba.</i>"
        )
        print("  🛡 " + msg.replace("\n", " | "))
        _tg(msg)

    print(f"  nuevos: {len(nuevos)} | patas seguras nuevas: {len(patas_nuevas)} | "
          f"combinadas nuevas: {len(combos_nuevos)} | "
          f"cierres fijados: {cierres} | liquidados: {liquidados} | "
          f"combinadas liquidadas: {combos_liquidados}")

    r = clv_db.resumen()
    linea = (f"picks {r['picks_total']} · pend {r['pendientes']} · "
             f"con cierre {r.get('con_linea_cierre', 0)} · liquidados {r.get('liquidados', 0)}")
    if "clv_ev_medio" in r:
        linea += (f"\nCLV EV medio {r['clv_ev_medio']:+.2%} · "
                  f"batió cierre {r['pct_batio_cierre']:.0%}")
    if "roi" in r:
        linea += f"\nROI papel {r['roi']:+.1%} ({r['pnl_u']:+.1f}u · WR {r['win_rate']:.0%})"

    rc = clv_db.resumen_combos()
    if rc["combos_total"]:
        linea += (f"\nCombinadas: {rc['combos_total']} total · {rc['pendientes']} pend · "
                  f"{rc['liquidadas']} liquidadas")
        if "win_rate_real" in rc:
            linea += (f"\n  WR real {rc['win_rate_real']:.0%} vs prometido "
                      f"{rc['prob_prometida_media']:.0%} · ROI {rc['roi']:+.1%}")

    print("  " + linea.replace("\n", "\n  "))
    if nuevos or combos_nuevos or cierres or liquidados or combos_liquidados:
        _tg(f"📊 <b>[SHARP] Estado</b>\n{linea}")

    if quota_restante_min is not None and quota_restante_min < QUOTA_ALERTA_BAJO:
        _tg(f"⚠️ <b>[SHARP] Cuota de The Odds API baja</b>\n"
            f"Quedan {quota_restante_min} requests este mes. Se resetea a fin de mes — "
            f"si se agota, sharp_live simplemente deja de traer cuotas nuevas hasta entonces.")


def _mostrar_picks() -> None:
    import sqlite3
    c = sqlite3.connect(clv_db.DB)
    c.row_factory = sqlite3.Row
    filas = c.execute(
        "SELECT * FROM picks ORDER BY (status='SETTLED'), commence_time").fetchall()
    if not filas:
        print("Sin picks todavía. Corré un ciclo: python sharp_live.py")
        return
    for r in filas:
        sel = (r["home"] if r["outcome"] == "local"
               else r["away"] if r["outcome"] == "visitante" else "Empate")
        est = r["result"] or ("cierre✓" if r["clv_ev"] is not None else "pendiente")
        clv = f" | CLV {r['clv_ev']:+.1%}" if r["clv_ev"] is not None else ""
        pnl = f" | {r['pnl_u']:+.2f}u" if r["pnl_u"] is not None else ""
        print(f"{r['commence_time'][:16].replace('T', ' ')}  "
              f"{r['home'][:16]:16} v {r['away'][:16]:16}  "
              f"{sel[:16]:16} @ {r['odds_taken']:<5} {r['book_taken'][:12]:12} "
              f"EV+{r['ev_pick']*100:.1f}%  [{est}]{clv}{pnl}")


def _mostrar_combos() -> None:
    import sqlite3
    c = sqlite3.connect(clv_db.DB)
    c.row_factory = sqlite3.Row
    combos = c.execute(
        "SELECT * FROM combos ORDER BY (status='SETTLED'), ts_creado").fetchall()
    if not combos:
        print("Sin combinadas todavía. Corré un ciclo: python sharp_live.py")
        return
    for combo in combos:
        legs = c.execute(
            """SELECT p.home, p.away, p.outcome, p.odds_taken, p.result FROM combo_legs cl
               JOIN picks p ON p.id = cl.pick_id WHERE cl.combo_id=?""",
            (combo["id"],)).fetchall()
        est = combo["result"] or "pendiente"
        pnl = f" | {combo['pnl_u']:+.2f}u" if combo["pnl_u"] is not None else ""
        print(f"#{combo['id']:<3} {combo['tipo']:8} cuota={combo['cuota_total']:<6} "
              f"prob_ajustada={combo['prob_ajustada']:.0%}  [{est}]{pnl}")
        for leg in legs:
            sel = (leg["home"] if leg["outcome"] == "local"
                   else leg["away"] if leg["outcome"] == "visitante" else "Empate")
            r = leg["result"] or "?"
            print(f"      - {leg['home'][:16]:16} v {leg['away'][:16]:16}  "
                  f"{sel[:16]:16} @ {leg['odds_taken']:<5} [{r}]")


def main() -> None:
    if "--resumen" in sys.argv:
        for k, v in clv_db.resumen().items():
            print(f"  {k}: {v}")
        print("  --- combinadas ---")
        for k, v in clv_db.resumen_combos().items():
            print(f"  {k}: {v}")
        return

    if "--picks" in sys.argv:
        _mostrar_picks()
        return

    if "--combos" in sys.argv:
        _mostrar_combos()
        return

    if not THE_ODDS_API_KEY:
        print("Falta THE_ODDS_API_KEY en .env")
        sys.exit(1)

    print("═══ SHARP LIVE — test forward +EV con ancla Pinnacle ═══")
    print("    Creado por Diego Aleman · 100% papel, ninguna apuesta real")
    _tg("🧪 <b>[SHARP] Test iniciado</b>\nMidiendo CLV vs Pinnacle en papel. "
        "Sin dinero real. Objetivo: 3-4 semanas de datos.")

    while True:
        try:
            ciclo()
        except KeyboardInterrupt:
            print("\nDetenido por el usuario.")
            break
        except Exception as e:  # noqa: BLE001
            print(f"  error en el ciclo: {e}")
            _tg(f"⚠️ <b>[SHARP] error</b>\n{str(e)[:300]}")
        if "--loop" not in sys.argv:
            break
        print(f"    durmiendo {SLEEP_HORAS}h…")
        time.sleep(SLEEP_HORAS * 3600)


if __name__ == "__main__":
    main()

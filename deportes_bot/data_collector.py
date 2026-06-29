# ============================================================
#  data_collector.py — Recolector de datos deportivos
#
#  Estrategia adaptada al plan Free de API-Football:
#    - El plan Free SOLO permite búsqueda por 'date'
#    - NO permite: league+season actual, team+last, headtohead
#    - Solución: buscamos día a día y filtramos localmente
# ============================================================

import logging
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests
import pandas as pd

from config import (
    API_FOOTBALL_KEY, RAPIDAPI_KEY, THE_ODDS_API_KEY,
    API_FOOTBALL_BASE, RAPIDAPI_HOST, THE_ODDS_API_BASE,
    LIGAS, LIGAS_PERMITIDAS, LIGAS_TEMPORADA, ODDS_SPORT_MAP,
    JORNADAS_HISTORICO, CSV_PARTIDOS, MOCK_MODE, LOG_FILE
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("DataCollector")

_req_count = 0
REQ_DAILY_SOFT_LIMIT = 85   # dejar margen sobre el límite de 100


def _get(endpoint: str, params: dict) -> dict:
    global _req_count
    if _req_count >= REQ_DAILY_SOFT_LIMIT:
        log.warning(f"Límite diario alcanzado ({REQ_DAILY_SOFT_LIMIT}). Parando peticiones.")
        return {}

    if RAPIDAPI_KEY:
        base    = f"https://{RAPIDAPI_HOST}"
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    else:
        base    = API_FOOTBALL_BASE
        headers = {"x-apisports-key": API_FOOTBALL_KEY}

    url = f"{base}/{endpoint}"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        _req_count += 1
        rem = r.headers.get("x-ratelimit-requests-remaining", "?")
        log.debug(f"GET {endpoint} params={params} → {r.status_code} | used={_req_count} rem={rem}")
        return r.json()
    except requests.RequestException as e:
        log.error(f"Error en {endpoint}: {e}")
        return {}


# ══════════════════════════════════════════
#  CACHE DE FIXTURES POR FECHA
#  Una sola pasada descarga todo: próximos partidos +
#  resultados recientes para calcular forma.
# ══════════════════════════════════════════

def _descargar_ventana_fechas(dias_atras: int = 14, dias_adelante: int = 7) -> list[dict]:
    """
    Descarga todos los fixtures de una ventana de fechas usando
    el endpoint /fixtures?date=... (único permitido en plan Free).
    Retorna lista plana de fixtures (crudos de la API).
    """
    today = datetime.now(timezone.utc).date()
    todos = []
    fechas = []
    for d in range(-dias_atras, dias_adelante + 1):
        fechas.append(today + timedelta(days=d))

    log.info(f"Descargando ventana {fechas[0]} → {fechas[-1]} ({len(fechas)} días)…")
    for dia in fechas:
        data = _get("fixtures", {"date": str(dia)})
        fixtures = data.get("response", [])
        todos.extend(fixtures)
        time.sleep(0.25)

    # Filtrar solo ligas permitidas
    filtrados = [f for f in todos if f.get("league", {}).get("id") in LIGAS_PERMITIDAS]
    log.info(f"  Total fixtures ventana: {len(todos)} | en ligas permitidas: {len(filtrados)}")
    return filtrados


def _build_team_stats_cache(fixtures: list[dict]) -> dict:
    """
    A partir de la lista de fixtures (crudos), calcula para cada equipo:
      gf     → promedio de goles a favor en los últimos partidos TERMINADOS
      gc     → promedio de goles en contra
      forma  → string 'WWDLW' (más reciente al final)
      h2h    → dict {(team1_id, team2_id): 'H3-D1-A2'}

    Retorna: {team_id: {gf, gc, forma}, "h2h": {(a,b): str}}
    """
    FT_STATUSES = {"FT", "AET", "PEN"}
    NS_STATUSES = {"NS", "TBD"}

    # Ordenar por fecha ascendente
    def fecha_fix(f):
        try:
            return f["fixture"]["date"][:10]
        except Exception:
            return "0000-00-00"

    fixtures_sorted = sorted(fixtures, key=fecha_fix)

    # Separar terminados y pendientes
    finished = [f for f in fixtures_sorted if f["fixture"].get("status", {}).get("short") in FT_STATUSES]
    upcoming = [f for f in fixtures_sorted if f["fixture"].get("status", {}).get("short") in NS_STATUSES]

    # Índice team_id → lista de resultados
    team_results: dict[int, list[dict]] = defaultdict(list)
    h2h: dict[tuple, list] = defaultdict(list)

    for f in finished:
        home_id = f["teams"]["home"]["id"]
        away_id = f["teams"]["away"]["id"]
        gh = f["goals"].get("home") or 0
        ga = f["goals"].get("away") or 0
        home_won = f["teams"]["home"].get("winner")

        for is_home, tid, gf, gc in [(True, home_id, gh, ga), (False, away_id, ga, gh)]:
            if home_won is True:
                res = "W" if is_home else "L"
            elif home_won is False:
                res = "L" if is_home else "W"
            else:
                res = "D"
            team_results[tid].append({"gf": gf, "gc": gc, "res": res})

        # H2H: guardamos desde perspectiva del home_id
        h2h[(home_id, away_id)].append({
            "gf": gh, "gc": ga,
            "home_won": home_won,
        })
        h2h[(away_id, home_id)].append({
            "gf": ga, "gc": gh,
            "home_won": None if home_won is None else (not home_won),
        })

    # Calcular estadísticas por equipo
    stats: dict[int, dict] = {}
    for tid, results in team_results.items():
        recent = results[-JORNADAS_HISTORICO:]
        if not recent:
            continue
        gf_avg = round(sum(r["gf"] for r in recent) / len(recent), 2)
        gc_avg = round(sum(r["gc"] for r in recent) / len(recent), 2)
        forma  = "".join(r["res"] for r in recent)
        stats[tid] = {"gf": gf_avg, "gc": gc_avg, "forma": forma}

    # Calcular H2H string
    h2h_str: dict[tuple, str] = {}
    for (t1, t2), games in h2h.items():
        hw = sum(1 for g in games if g["home_won"] is True)
        d  = sum(1 for g in games if g["home_won"] is None)
        aw = sum(1 for g in games if g["home_won"] is False)
        h2h_str[(t1, t2)] = f"H{hw}-D{d}-A{aw}"

    return {"teams": stats, "h2h": h2h_str, "upcoming": upcoming}


def _default_form() -> dict:
    return {"gf": 1.2, "gc": 1.0, "forma": ""}


def _parse_fixture(fix: dict) -> dict:
    f  = fix.get("fixture", {})
    l  = fix.get("league",  {})
    t  = fix.get("teams",   {})
    raw = f.get("date", "2000-01-01T00:00:00+00:00")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    return {
        "fixture_id":           f.get("id"),
        "fecha":                dt.strftime("%Y-%m-%d"),
        "hora":                 dt.strftime("%H:%M"),
        "liga_id":              l.get("id"),
        "liga_nombre":          l.get("name"),
        "equipo_local_id":      t.get("home", {}).get("id"),
        "equipo_local":         t.get("home", {}).get("name"),
        "equipo_visitante_id":  t.get("away", {}).get("id"),
        "equipo_visitante":     t.get("away", {}).get("name"),
        "cuota_local":          None,
        "cuota_empate":         None,
        "cuota_visitante":      None,
        "gf_local_5j":          None,
        "gc_local_5j":          None,
        "gf_visitante_5j":      None,
        "gc_visitante_5j":      None,
        "forma_local":          "",
        "forma_visitante":      "",
        "h2h_historial":        "H0-D0-A0",
    }


# ══════════════════════════════════════════
#  CUOTAS (The Odds API)
# ══════════════════════════════════════════

def _get_odds_for_liga(liga_id: int) -> list[dict]:
    sport = ODDS_SPORT_MAP.get(liga_id)
    if not sport or not THE_ODDS_API_KEY:
        return []
    url    = f"{THE_ODDS_API_BASE}/sports/{sport}/odds"
    params = {"apiKey": THE_ODDS_API_KEY, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        rem = r.headers.get("x-requests-remaining", "?")
        log.info(f"Odds '{sport}': {len(r.json())} eventos | remaining={rem}")
        return r.json()
    except requests.RequestException as e:
        log.error(f"Odds API error: {e}")
        return []


def _match_odds(home_name: str, away_name: str, odds_events: list) -> dict:
    """
    Retorna best price (máxima cuota entre bookmakers) Y precio consenso
    (promedio sin margen) para detectar value por discrepancia de mercado.
    """
    def sim(a: str, b: str) -> bool:
        a, b = a.lower(), b.lower()
        return a in b or b in a or a[:5] == b[:5]

    for ev in odds_events:
        if sim(ev.get("home_team", ""), home_name) and sim(ev.get("away_team", ""), away_name):
            all_local: list[float] = []
            all_empate: list[float] = []
            all_visit: list[float] = []

            for bk in ev.get("bookmakers", []):
                for mkt in bk.get("markets", []):
                    if mkt.get("key") != "h2h":
                        continue
                    for o in mkt.get("outcomes", []):
                        nm    = o.get("name", "").lower()
                        price = float(o.get("price", 0))
                        if price <= 1.0:
                            continue
                        if sim(ev.get("home_team", ""), nm):
                            all_local.append(price)
                        elif nm == "draw":
                            all_empate.append(price)
                        else:
                            all_visit.append(price)

            if not all_local:
                continue

            # Mejor precio disponible
            best = {
                "local":     max(all_local),
                "empate":    max(all_empate) if all_empate else 0.0,
                "visitante": max(all_visit)  if all_visit  else 0.0,
            }

            # Precio promedio de todos los bookmakers
            avg_l = sum(all_local)  / len(all_local)
            avg_e = sum(all_empate) / len(all_empate) if all_empate else 3.5
            avg_v = sum(all_visit)  / len(all_visit)  if all_visit  else 3.5

            # No-vig: probabilidad consenso real (sin margen de la casa)
            inv_sum = 1/avg_l + 1/avg_e + 1/avg_v
            best["cons_prob_local"]     = round((1/avg_l) / inv_sum, 4)
            best["cons_prob_empate"]    = round((1/avg_e) / inv_sum, 4)
            best["cons_prob_visitante"] = round((1/avg_v) / inv_sum, 4)
            best["n_bookmakers"]        = max(len(all_local), len(all_empate), len(all_visit))
            return best
    return {}


# ══════════════════════════════════════════
#  DATOS MOCK
# ══════════════════════════════════════════

def _mock_partidos() -> list[dict]:
    import random
    log.info("MOCK MODE → datos ficticios")
    rng   = random.Random(42)
    today = datetime.now()
    data  = [
        (1,"FIFA World Cup","Brazil","Japan",      1.77,3.80,5.80, 2.20,0.80,0.80,1.80,"WWWDW","WLLWL","H4-D1-A1"),
        (1,"FIFA World Cup","Germany","Paraguay",  1.38,5.75,12.0, 2.50,0.60,0.60,2.20,"WWWWW","LLLWL","H8-D1-A1"),
        (1,"FIFA World Cup","Netherlands","Morocco",2.34,3.33,3.80,1.60,1.20,1.40,1.40,"WDWWL","WDWLW","H3-D2-A2"),
        (1,"FIFA World Cup","Ivory Coast","Norway", 4.00,3.69,2.10,1.20,1.60,1.80,1.20,"LWWDL","WWWDL","H1-D2-A4"),
        (1,"FIFA World Cup","France","Sweden",      1.31,6.62,13.5,2.80,0.40,0.60,2.40,"WWWWW","LLLLL","H9-D1-A0"),
    ]
    partidos = []
    for i, (lid,liga,home,away,cl,ce,cv,gfh,gch,gfa,gca,fh,fa,h2h) in enumerate(data):
        dt = today + timedelta(days=i % 3, hours=rng.choice([17,20]))
        partidos.append({
            "fixture_id": 99000+i, "fecha": dt.strftime("%Y-%m-%d"), "hora": dt.strftime("%H:%M"),
            "liga_id": lid, "liga_nombre": liga,
            "equipo_local_id": 9000+i, "equipo_local": home,
            "equipo_visitante_id": 9050+i, "equipo_visitante": away,
            "cuota_local": cl, "cuota_empate": ce, "cuota_visitante": cv,
            "gf_local_5j": gfh, "gc_local_5j": gch,
            "gf_visitante_5j": gfa, "gc_visitante_5j": gca,
            "forma_local": fh, "forma_visitante": fa, "h2h_historial": h2h,
        })
    return partidos


# ══════════════════════════════════════════
#  FUNCIÓN PRINCIPAL
# ══════════════════════════════════════════

def recolectar_datos() -> pd.DataFrame:
    log.info("═" * 50)
    log.info("Iniciando recolección de datos")

    if MOCK_MODE:
        partidos = _mock_partidos()
    else:
        # ── 1. Cuotas (The Odds API) ─────────────────────
        odds_cache: dict[int, list] = {}
        for liga_id in LIGAS_PERMITIDAS:
            odds_cache[liga_id] = _get_odds_for_liga(liga_id)

        # ── 2. Descarga ventana de fechas (una sola pasada) ──
        todos_fixtures = _descargar_ventana_fechas(dias_atras=14, dias_adelante=7)
        cache = _build_team_stats_cache(todos_fixtures)
        upcoming = cache["upcoming"]
        team_stats = cache["teams"]
        h2h_map   = cache["h2h"]

        log.info(f"Próximos partidos en ligas permitidas: {len(upcoming)}")
        if not upcoming:
            log.warning("No hay partidos próximos. Comprueba LIGAS_PERMITIDAS y las fechas.")

        partidos = []
        for fix in upcoming:
            p = _parse_fixture(fix)
            home_id = p["equipo_local_id"]
            away_id = p["equipo_visitante_id"]
            liga_id = p["liga_id"]

            # Forma local
            fh = team_stats.get(home_id, _default_form())
            p["gf_local_5j"]  = fh["gf"]
            p["gc_local_5j"]  = fh["gc"]
            p["forma_local"]  = fh["forma"]

            # Forma visitante
            fa = team_stats.get(away_id, _default_form())
            p["gf_visitante_5j"]  = fa["gf"]
            p["gc_visitante_5j"]  = fa["gc"]
            p["forma_visitante"]  = fa["forma"]

            # H2H
            p["h2h_historial"] = h2h_map.get((home_id, away_id), "H0-D0-A0")

            # Cuotas
            cuotas = _match_odds(p["equipo_local"], p["equipo_visitante"],
                                 odds_cache.get(liga_id, []))
            if cuotas:
                p["cuota_local"]          = cuotas.get("local")
                p["cuota_empate"]         = cuotas.get("empate")
                p["cuota_visitante"]      = cuotas.get("visitante")
                p["cons_prob_local"]      = cuotas.get("cons_prob_local")
                p["cons_prob_empate"]     = cuotas.get("cons_prob_empate")
                p["cons_prob_visitante"]  = cuotas.get("cons_prob_visitante")
                p["n_bookmakers"]         = cuotas.get("n_bookmakers", 1)
                log.info(f"  {p['equipo_local']} best={cuotas.get('local')} cons_p={cuotas.get('cons_prob_local'):.1%} | "
                         f"X best={cuotas.get('empate')} | "
                         f"{p['equipo_visitante']} best={cuotas.get('visitante')} cons_p={cuotas.get('cons_prob_visitante'):.1%} | "
                         f"N bookmakers={cuotas.get('n_bookmakers')} | "
                         f"forma_h='{p['forma_local']}' forma_a='{p['forma_visitante']}'")
            else:
                log.warning(f"  Sin cuotas: {p['equipo_local']} vs {p['equipo_visitante']}")

            partidos.append(p)

        log.info(f"Peticiones API-Football usadas: {_req_count}")

    # ── CSV ───────────────────────────────────────────────
    df = pd.DataFrame(partidos)
    defaults = {
        "cuota_local": 2.0, "cuota_empate": 3.3, "cuota_visitante": 3.5,
        "gf_local_5j": 1.2, "gc_local_5j": 1.0,
        "gf_visitante_5j": 1.0, "gc_visitante_5j": 1.2,
        "h2h_historial": "H0-D0-A0", "forma_local": "", "forma_visitante": "",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = df[col].fillna(default)

    CSV_PARTIDOS.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV_PARTIDOS, index=False, encoding="utf-8")
    log.info(f"✓ {len(df)} partidos guardados → {CSV_PARTIDOS}")
    return df


if __name__ == "__main__":
    df = recolectar_datos()
    cols = ["fecha","hora","liga_nombre","equipo_local","equipo_visitante",
            "cuota_local","cuota_empate","cuota_visitante",
            "gf_local_5j","gc_local_5j","gf_visitante_5j","gc_visitante_5j",
            "forma_local","forma_visitante","h2h_historial"]
    print(df[[c for c in cols if c in df.columns]].to_string(index=False))

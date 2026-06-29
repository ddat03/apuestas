# ============================================================
#  markets_collector.py — Recolección de múltiples mercados
#
#  Mercados soportados via The Odds API:
#    h2h       → Resultado 1X2
#    totals    → Más/Menos goles (Over/Under 2.5)
#    btts      → Ambos equipos marcan (Yes/No)
#    spreads   → Handicap asiático
#
#  Mercados estimados via OpenAI (no tienen API pública gratuita):
#    corners   → Más/Menos esquinas
#    cards     → Más/Menos tarjetas
#    first_half → Resultado al descanso
# ============================================================

import logging
import requests
from dataclasses import dataclass, field, asdict

from config import THE_ODDS_API_KEY, THE_ODDS_API_BASE, ODDS_SPORT_MAP, LOG_FILE

log = logging.getLogger("MarketsCollector")


# ══════════════════════════════════════════
#  ESTRUCTURA DE MERCADO
# ══════════════════════════════════════════

@dataclass
class Mercado:
    """Un mercado de apuestas con sus cuotas disponibles."""
    nombre:     str              # ej: "over_2_5", "btts_yes", "handicap_-1"
    etiqueta:   str              # ej: "Más de 2.5 goles"
    cuota_best: float = 0.0     # mejor cuota disponible
    cuota_avg:  float = 0.0     # cuota media entre bookmakers
    prob_cons:  float = 0.0     # probabilidad consenso (no-vig)
    n_books:    int   = 0       # cuántos bookmakers ofrecen esta línea
    fuente:     str   = "api"   # "api" | "ai_estimate"


@dataclass
class PartidoMercados:
    """Todos los mercados disponibles para un partido."""
    fixture_id:       int
    equipo_local:     str
    equipo_visitante: str
    fecha:            str
    liga:             str

    # Mercados API
    h2h:          dict[str, Mercado] = field(default_factory=dict)   # local/draw/away
    totals:       dict[str, Mercado] = field(default_factory=dict)   # over_X / under_X
    btts:         dict[str, Mercado] = field(default_factory=dict)   # yes / no
    spreads:      dict[str, Mercado] = field(default_factory=dict)   # handicap lines

    # Mercados estimados por IA (se rellenan en ai_analyzer.py)
    corners:      dict[str, Mercado] = field(default_factory=dict)
    cards:        dict[str, Mercado] = field(default_factory=dict)
    first_half:   dict[str, Mercado] = field(default_factory=dict)


# ══════════════════════════════════════════
#  UTILIDADES
# ══════════════════════════════════════════

def _no_vig_prob(precios: list[float]) -> float:
    """Probabilidad no-vig del outcome dado sus precios entre bookmakers."""
    if not precios:
        return 0.0
    avg = sum(precios) / len(precios)
    return round(1.0 / avg, 4) if avg > 0 else 0.0


def _normalizar_no_vig(probs: list[float]) -> list[float]:
    """Normaliza probabilidades para que sumen exactamente 1.0 (elimina margen)."""
    total = sum(probs)
    return [round(p / total, 4) for p in probs] if total > 0 else probs


def _procesar_h2h(evento: dict, home: str, away: str) -> dict[str, Mercado]:
    all_local, all_emp, all_away = [], [], []

    def is_home(name: str) -> bool:
        n = name.lower()
        return home.lower()[:5] in n or n in home.lower()

    for bk in evento.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt["key"] != "h2h":
                continue
            for o in mkt["outcomes"]:
                p = float(o.get("price", 0))
                if p <= 1.0:
                    continue
                nm = o["name"].lower()
                if "draw" in nm:
                    all_emp.append(p)
                elif is_home(o["name"]):
                    all_local.append(p)
                else:
                    all_away.append(p)

    if not all_local:
        return {}

    pl = _no_vig_prob(all_local)
    pe = _no_vig_prob(all_emp)
    pa = _no_vig_prob(all_away)
    pl_n, pe_n, pa_n = _normalizar_no_vig([pl, pe, pa])

    return {
        "local": Mercado("h2h_local", f"{home} gana",
                         max(all_local), sum(all_local)/len(all_local), pl_n, len(all_local)),
        "empate": Mercado("h2h_empate", "Empate",
                          max(all_emp) if all_emp else 0, sum(all_emp)/max(len(all_emp),1), pe_n, len(all_emp)),
        "visitante": Mercado("h2h_visitante", f"{away} gana",
                             max(all_away) if all_away else 0, sum(all_away)/max(len(all_away),1), pa_n, len(all_away)),
    }


def _procesar_totals(evento: dict) -> dict[str, Mercado]:
    """Procesa líneas de Over/Under. Devuelve dict con todas las líneas encontradas."""
    lineas: dict[str, dict] = {}  # "2.5" → {over: [...], under: [...]}

    for bk in evento.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt["key"] not in ("totals", "alternate_totals"):
                continue
            for o in mkt["outcomes"]:
                punto = str(o.get("point", "2.5"))
                name  = o["name"].lower()
                precio= float(o.get("price", 0))
                if precio <= 1.0:
                    continue
                lineas.setdefault(punto, {"over": [], "under": []})
                if "over" in name:
                    lineas[punto]["over"].append(precio)
                elif "under" in name:
                    lineas[punto]["under"].append(precio)

    result: dict[str, Mercado] = {}
    for punto, datos in lineas.items():
        over_list  = datos["over"]
        under_list = datos["under"]
        if not over_list or not under_list:
            continue

        po = _no_vig_prob(over_list)
        pu = _no_vig_prob(under_list)
        po_n, pu_n = _normalizar_no_vig([po, pu])

        key_over  = f"over_{punto.replace('.','_')}"
        key_under = f"under_{punto.replace('.','_')}"
        result[key_over]  = Mercado(key_over,  f"Más de {punto} goles",
                                    max(over_list),  sum(over_list)/len(over_list),  po_n, len(over_list))
        result[key_under] = Mercado(key_under, f"Menos de {punto} goles",
                                    max(under_list), sum(under_list)/len(under_list), pu_n, len(under_list))
    return result


def _procesar_btts(evento: dict) -> dict[str, Mercado]:
    yes_list, no_list = [], []

    for bk in evento.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt["key"] != "btts":
                continue
            for o in mkt["outcomes"]:
                p  = float(o.get("price", 0))
                nm = o["name"].lower()
                if p <= 1.0:
                    continue
                if nm == "yes":
                    yes_list.append(p)
                elif nm == "no":
                    no_list.append(p)

    if not yes_list:
        return {}

    py = _no_vig_prob(yes_list)
    pn = _no_vig_prob(no_list)
    py_n, pn_n = _normalizar_no_vig([py, pn])

    return {
        "yes": Mercado("btts_yes", "Ambos marcan — Sí", max(yes_list), sum(yes_list)/len(yes_list), py_n, len(yes_list)),
        "no":  Mercado("btts_no",  "Ambos marcan — No", max(no_list)  if no_list else 0,
                       sum(no_list)/max(len(no_list),1), pn_n, len(no_list)),
    }


def _procesar_spreads(evento: dict) -> dict[str, Mercado]:
    """Handicap asiático: agrupa por punto y equipo."""
    lineas: dict[str, list[float]] = {}

    for bk in evento.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt["key"] not in ("spreads", "alternate_spreads"):
                continue
            for o in mkt["outcomes"]:
                p     = float(o.get("price", 0))
                punto = o.get("point", 0)
                name  = o["name"]
                if p <= 1.0:
                    continue
                clave = f"{name}_{punto}"
                lineas.setdefault(clave, []).append(p)

    result: dict[str, Mercado] = {}
    for clave, precios in lineas.items():
        if len(precios) < 2:
            continue
        prob = _no_vig_prob(precios)
        safe_key = clave.replace(" ", "_").replace(".", "_").replace("-", "m")
        result[safe_key] = Mercado(safe_key, f"Hándicap {clave}",
                                   max(precios), sum(precios)/len(precios), prob, len(precios))
    return result


# ══════════════════════════════════════════
#  FUNCIÓN PRINCIPAL
# ══════════════════════════════════════════

def descargar_todos_mercados(liga_id: int) -> list[dict]:
    """
    Descarga TODOS los eventos con mercados para una liga de una sola vez.
    Usar esto en lugar de llamar recolectar_mercados partido a partido.
    Ahorra N-1 peticiones a la API.
    """
    sport = ODDS_SPORT_MAP.get(liga_id)
    if not sport or not THE_ODDS_API_KEY:
        return []

    MARKET_FALLBACKS = [
        "h2h,totals,btts,spreads",
        "h2h,totals,btts",
        "h2h,totals",
        "h2h",
    ]
    url = f"{THE_ODDS_API_BASE}/sports/{sport}/odds"

    for market_keys in MARKET_FALLBACKS:
        params = {
            "apiKey":    THE_ODDS_API_KEY,
            "regions":   "eu",
            "markets":   market_keys,
            "oddsFormat":"decimal",
        }
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            eventos = r.json()
            rem = r.headers.get("x-requests-remaining", "?")
            log.info(f"Mercados bulk ({market_keys}): {len(eventos)} eventos | remaining={rem}")
            return eventos
        except requests.HTTPError:
            if r.status_code == 422:
                log.warning(f"'{market_keys}' no disponible para {sport} → fallback…")
                continue
            log.error(f"HTTP {r.status_code} al descargar mercados bulk")
            return []
        except requests.RequestException as e:
            log.error(f"Error mercados bulk: {e}")
            return []
    return []


def recolectar_mercados(
    partido: dict,
    liga_id: int,
    eventos_cache: list[dict] | None = None,
) -> PartidoMercados:
    """
    Recolecta todos los mercados disponibles para un partido.
    Si se pasa eventos_cache (de descargar_todos_mercados), no hace petición extra.
    """
    home = partido.get("equipo_local", "")
    away = partido.get("equipo_visitante", "")

    pm = PartidoMercados(
        fixture_id       = partido.get("fixture_id", 0),
        equipo_local     = home,
        equipo_visitante = away,
        fecha            = partido.get("fecha", ""),
        liga             = partido.get("liga_nombre", ""),
    )

    # Usar cache si se pasó (evita múltiples peticiones API)
    if eventos_cache is not None:
        eventos = eventos_cache
    else:
        sport = ODDS_SPORT_MAP.get(liga_id)
        if not sport or not THE_ODDS_API_KEY:
            log.warning(f"Sin The Odds API key o sport no mapeado (liga_id={liga_id})")
            return pm
        eventos = descargar_todos_mercados(liga_id)

    if not eventos:
        return pm

    # Buscar el evento correcto
    def sim(a: str, b: str) -> bool:
        a, b = a.lower(), b.lower()
        return a in b or b in a or a[:5] == b[:5]

    for ev in eventos:
        if sim(ev.get("home_team",""), home) and sim(ev.get("away_team",""), away):
            pm.h2h     = _procesar_h2h(ev, home, away)
            pm.totals  = _procesar_totals(ev)
            pm.btts    = _procesar_btts(ev)
            pm.spreads = _procesar_spreads(ev)
            log.info(
                f"  {home} vs {away}: "
                f"h2h={len(pm.h2h)} | totals={len(pm.totals)} | "
                f"btts={len(pm.btts)} | spreads={len(pm.spreads)}"
            )
            break

    return pm


def mercados_a_dict(pm: PartidoMercados) -> dict:
    """Convierte PartidoMercados a dict plano (para JSON / prompts)."""
    return {
        "partido":       f"{pm.equipo_local} vs {pm.equipo_visitante}",
        "fecha":         pm.fecha,
        "liga":          pm.liga,
        "h2h":           {k: asdict(v) for k, v in pm.h2h.items()},
        "totals":        {k: asdict(v) for k, v in pm.totals.items()},
        "btts":          {k: asdict(v) for k, v in pm.btts.items()},
        "spreads":       {k: asdict(v) for k, v in pm.spreads.items()},
        "corners":       {k: asdict(v) for k, v in pm.corners.items()},
        "cards":         {k: asdict(v) for k, v in pm.cards.items()},
        "first_half":    {k: asdict(v) for k, v in pm.first_half.items()},
    }

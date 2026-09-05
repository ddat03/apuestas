# ============================================================
#  sofascore_client.py — Forma y H2H vía Sofascore (sin API key)
#
#  Creado por Diego Aleman.
#
#  Reemplaza a la parte de stats_analyzer.py que usaba API-Football
#  para forma/H2H — esa cuenta quedó bloqueada, y encima el plan free
#  nunca tuvo head-to-head directo (solo fixtures por fecha, forma
#  se armaba a mano cruzando un cache local).
#
#  Sofascore expone la MISMA información sin key ni login:
#    - /search/all?q=<nombre>      → id de equipo
#    - /team/{id}/events/next/0    → próximos partidos (para ubicar el
#                                     evento y de ahí sacar el H2H)
#    - /team/{id}/events/last/0    → últimos partidos (para forma)
#    - /event/{id}/h2h             → resumen de enfrentamientos directos,
#                                     YA calculado relativo al local/
#                                     visitante de ESE partido puntual
#                                     (no hace falta normalizar a mano
#                                     como con el h2h crudo de API-Football)
#
#  Un solo problema real: Sofascore bloquea requests con fingerprint
#  TLS de librería (Cloudflare devuelve 403 con `requests` plano,
#  verificado a mano el 2026-09-05). Por eso esto usa curl_cffi
#  (impersonate="chrome124"), que sí pasa — no hace falta headers
#  especiales más allá de eso.
# ============================================================

import logging
import re
import unicodedata
from datetime import datetime

from curl_cffi import requests as creq

from stats_analyzer import FormaEquipo, H2H

log = logging.getLogger("SofascoreClient")

BASE = "https://www.sofascore.com/api/v1"
IMPERSONATE = "chrome124"
TIMEOUT = 15


def _get(path: str) -> dict | None:
    try:
        r = creq.get(f"{BASE}{path}", impersonate=IMPERSONATE, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        log.warning(f"{path}: {e}")
        return None


def _normalizar(nombre: str) -> str:
    n = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().lower()
    for suf in (" fc", " cf", " sc", " afc", " ac", "fc ", "cf "):
        n = n.replace(suf, " ")
    return re.sub(r"\s+", " ", n).strip()


def _mismo_equipo(a: str, b: str) -> bool:
    na, nb = _normalizar(a), _normalizar(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def buscar_equipo_id(nombre: str) -> int | None:
    """Busca el equipo de fútbol que mejor matchea `nombre`. Sofascore
    ya devuelve los resultados ordenados por relevancia — nos quedamos
    con el primer resultado tipo "team" de fútbol."""
    data = _get(f"/search/all?q={nombre}")
    if not data:
        return None
    for res in data.get("results", []):
        entidad = res.get("entity", {})
        if res.get("type") == "team" and entidad.get("sport", {}).get("id") == 1:
            return entidad.get("id")
    return None


def buscar_evento_proximo(equipo_id: int, rival_nombre: str,
                          commence_time_iso: str, ventana_horas: float = 30) -> dict | None:
    """Entre los próximos partidos de `equipo_id`, ubica el que enfrenta
    a `rival_nombre` cerca de `commence_time_iso` (kickoff de The Odds API).
    Sirve para conseguir el event_id de Sofascore y de ahí pedir el H2H."""
    data = _get(f"/team/{equipo_id}/events/next/0")
    if not data:
        return None

    try:
        commence_ts = datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        commence_ts = None

    for ev in data.get("events", []):
        home, away = ev.get("homeTeam", {}), ev.get("awayTeam", {})
        rival = away if home.get("id") == equipo_id else home
        if not _mismo_equipo(rival.get("name", ""), rival_nombre):
            continue
        if commence_ts and abs(ev.get("startTimestamp", 0) - commence_ts) > ventana_horas * 3600:
            continue
        return ev
    return None


def forma_equipo(equipo_id: int, equipo_nombre: str, n: int = 5) -> FormaEquipo:
    """Últimos `n` partidos TERMINADOS del equipo — mismo cálculo y
    misma dataclass que stats_analyzer.obtener_forma, para no tener
    que tocar confidence_compositor.py ni stats_confluence.py."""
    forma = FormaEquipo(equipo_id=equipo_id, equipo_nombre=equipo_nombre)
    data = _get(f"/team/{equipo_id}/events/last/0")
    if not data:
        return forma

    partidos = [e for e in data.get("events", []) if e.get("status", {}).get("type") == "finished"][:n]
    forma.partidos = len(partidos)
    simbolos = []

    for e in partidos:
        es_local = e.get("homeTeam", {}).get("id") == equipo_id
        gf = (e.get("homeScore") if es_local else e.get("awayScore", {})).get("current", 0) or 0
        gc = (e.get("awayScore") if es_local else e.get("homeScore", {})).get("current", 0) or 0

        forma.goles_favor  += gf
        forma.goles_contra += gc

        if gf > gc:
            forma.victorias += 1
            simbolos.append("V")
            forma.puntos_forma += 3
        elif gf == gc:
            forma.empates += 1
            simbolos.append("E")
            forma.puntos_forma += 1
        else:
            forma.derrotas += 1
            simbolos.append("D")

    if forma.partidos > 0:
        forma.goles_favor  = round(forma.goles_favor  / forma.partidos, 2)
        forma.goles_contra = round(forma.goles_contra / forma.partidos, 2)

    forma.forma_str = "".join(simbolos)
    max_pts = forma.partidos * 3
    if max_pts > 0:
        ratio = forma.puntos_forma / max_pts
        forma.bonus = round((ratio - 0.5) * 0.10, 4)

    return forma


def h2h_evento(event_id: int, home_nombre: str, away_nombre: str) -> H2H:
    """H2H de un evento puntual de Sofascore — el endpoint YA lo da
    relativo al local/visitante de ESE partido (no hace falta revisar
    partido por partido quién jugó de local en cada enfrentamiento
    pasado, como con el h2h crudo de API-Football)."""
    h2h = H2H()
    data = _get(f"/event/{event_id}/h2h")
    duelo = (data or {}).get("teamDuel")
    if not duelo:
        h2h.resumen = "Sin H2H disponible"
        return h2h

    h2h.victorias_local  = duelo.get("homeWins", 0)
    h2h.victorias_visita = duelo.get("awayWins", 0)
    h2h.empates          = duelo.get("draws", 0)
    h2h.partidos_totales = h2h.victorias_local + h2h.victorias_visita + h2h.empates

    if h2h.partidos_totales == 0:
        h2h.resumen = "Sin H2H disponible"
        return h2h

    pct_l = h2h.victorias_local  / h2h.partidos_totales
    pct_v = h2h.victorias_visita / h2h.partidos_totales

    if pct_l >= 0.55:
        h2h.domina = "local"
        h2h.bonus_local = round((pct_l - 0.5) * 0.08, 4)
    elif pct_v >= 0.55:
        h2h.domina = "visitante"
        h2h.bonus_local = round(-(pct_v - 0.5) * 0.08, 4)
    else:
        h2h.domina = "equilibrado"

    h2h.resumen = (
        f"{home_nombre} {h2h.victorias_local}V-{h2h.empates}E-{h2h.victorias_visita}D "
        f"{away_nombre} (últimos {h2h.partidos_totales})"
    )
    return h2h

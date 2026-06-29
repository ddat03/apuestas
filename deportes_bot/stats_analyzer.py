# ============================================================
#  stats_analyzer.py — Forma, H2H, árbitro, descanso, clima
#
#  Variables tácticas y externas que afectan el resultado:
#    - Forma reciente (últimos 5 partidos en el torneo)
#    - Head-to-head histórico
#    - Estadísticas de árbitro (tarjetas, penales pitados)
#    - Días de descanso entre partidos
#    - Clima y temperatura (OpenWeatherMap)
#    - Tipo de partido (eliminatorio vs ya clasificado)
# ============================================================

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

from config import API_FOOTBALL_KEY, API_FOOTBALL_BASE, LOG_FILE

log = logging.getLogger("StatsAnalyzer")

HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}
OPENWEATHER_KEY = ""  # Opcional: añadir OPENWEATHER_KEY al .env


def _get(endpoint: str, params: dict) -> list:
    try:
        r = requests.get(f"{API_FOOTBALL_BASE}/{endpoint}",
                         headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            return []
        return data.get("response", [])
    except Exception as e:
        log.error(f"{endpoint}: {e}")
        return []


# ══════════════════════════════════════════
#  FORMA RECIENTE
# ══════════════════════════════════════════

@dataclass
class FormaEquipo:
    equipo_id:      int
    equipo_nombre:  str
    partidos:       int   = 0
    victorias:      int   = 0
    empates:        int   = 0
    derrotas:       int   = 0
    goles_favor:    float = 0.0
    goles_contra:   float = 0.0
    forma_str:      str   = ""    # ej: "VVDVD"
    puntos_forma:   float = 0.0   # 0-15 (5 partidos × 3 pts)
    bonus:          float = 0.0   # bonus/penalización para el score


def obtener_forma(equipo_id: int, equipo_nombre: str,
                  liga_id: int, season: str,
                  fixture_ids_cache: list[dict]) -> FormaEquipo:
    """
    Calcula la forma de un equipo con los últimos 5 partidos disponibles
    en el cache local (ya descargados en data_collector).

    fixture_ids_cache: lista de fixtures de la ventana de fechas.
    """
    forma = FormaEquipo(equipo_id=equipo_id, equipo_nombre=equipo_nombre)

    partidos_equipo = [
        f for f in fixture_ids_cache
        if (f.get("teams", {}).get("home", {}).get("id") == equipo_id or
            f.get("teams", {}).get("away", {}).get("id") == equipo_id)
        and f.get("fixture", {}).get("status", {}).get("short") == "FT"
    ]

    # Últimos 5 terminados
    partidos_equipo = sorted(
        partidos_equipo,
        key=lambda x: x.get("fixture", {}).get("date", ""),
        reverse=True
    )[:5]

    forma.partidos = len(partidos_equipo)
    simbolos = []

    for f in partidos_equipo:
        home_id    = f["teams"]["home"]["id"]
        home_goals = f["goals"]["home"] or 0
        away_goals = f["goals"]["away"] or 0

        if equipo_id == home_id:
            gf, gc = home_goals, away_goals
        else:
            gf, gc = away_goals, home_goals

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

    # Bonus: entre -0.05 y +0.05 según forma
    max_pts = forma.partidos * 3
    if max_pts > 0:
        ratio = forma.puntos_forma / max_pts
        forma.bonus = round((ratio - 0.5) * 0.10, 4)  # -5% a +5%

    log.info(f"{equipo_nombre}: forma={forma.forma_str} gf={forma.goles_favor} gc={forma.goles_contra}")
    return forma


# ══════════════════════════════════════════
#  HEAD-TO-HEAD
# ══════════════════════════════════════════

@dataclass
class H2H:
    partidos_totales: int   = 0
    victorias_local:  int   = 0
    empates:          int   = 0
    victorias_visita: int   = 0
    domina:           str   = "equilibrado"  # "local" | "visitante" | "equilibrado"
    bonus_local:      float = 0.0
    resumen:          str   = ""


def obtener_h2h(equipo_local_id: int, equipo_visita_id: int,
                equipo_local_nombre: str, equipo_visita_nombre: str,
                n_partidos: int = 10) -> H2H:
    """Historial de enfrentamientos directos (funciona bien para ligas europeas)."""
    h2h = H2H()
    datos = _get("fixtures/headtohead", {
        "h2h": f"{equipo_local_id}-{equipo_visita_id}",
        "last": n_partidos,
        "status": "FT",
    })

    if not datos:
        h2h.resumen = "Sin H2H disponible"
        return h2h

    h2h.partidos_totales = len(datos)
    for f in datos:
        home_id    = f["teams"]["home"]["id"]
        home_goals = f["goals"]["home"] or 0
        away_goals = f["goals"]["away"] or 0

        # Determinar si el equipo_local ganó (independientemente de si jugó en casa)
        if home_id == equipo_local_id:
            gf, gc = home_goals, away_goals
        else:
            gf, gc = away_goals, home_goals

        if gf > gc:
            h2h.victorias_local += 1
        elif gf == gc:
            h2h.empates += 1
        else:
            h2h.victorias_visita += 1

    total = h2h.partidos_totales
    pct_l = h2h.victorias_local  / total
    pct_v = h2h.victorias_visita / total

    if pct_l >= 0.55:
        h2h.domina = "local"
        h2h.bonus_local = round((pct_l - 0.5) * 0.08, 4)   # max +4%
    elif pct_v >= 0.55:
        h2h.domina = "visitante"
        h2h.bonus_local = round(-(pct_v - 0.5) * 0.08, 4)  # max -4%
    else:
        h2h.domina = "equilibrado"

    h2h.resumen = (
        f"{equipo_local_nombre} {h2h.victorias_local}V-{h2h.empates}E-{h2h.victorias_visita}D "
        f"{equipo_visita_nombre} (últimos {total})"
    )
    log.info(f"H2H: {h2h.resumen}")
    return h2h


# ══════════════════════════════════════════
#  ÁRBITRO
# ══════════════════════════════════════════

@dataclass
class EstadisticasArbitro:
    nombre:           str   = ""
    partidos_analizados: int = 0
    tarjetas_amarillas_prom: float = 0.0
    tarjetas_rojas_prom:     float = 0.0
    penales_prom:            float = 0.0
    estilo:           str   = "neutro"  # "estricto" | "permisivo" | "neutro"
    resumen:          str   = ""


def obtener_estadisticas_arbitro(fixture_ids_recientes: list[int],
                                  arbitro_nombre: str) -> EstadisticasArbitro:
    """
    Construye estadísticas del árbitro a partir de los partidos del cache.
    Los partidos del árbitro se buscan dentro de los ya descargados.
    """
    stats = EstadisticasArbitro(nombre=arbitro_nombre)

    if not arbitro_nombre:
        stats.resumen = "Árbitro no asignado aún"
        return stats

    total_amarillas = 0
    total_rojas     = 0
    total_penales   = 0
    analizados      = 0

    for fix_id in fixture_ids_recientes:
        eventos = _get("fixtures/events", {"fixture": fix_id})
        if not eventos:
            continue
        analizados += 1
        for ev in eventos:
            tipo    = ev.get("type", "").lower()
            detalle = ev.get("detail", "").lower()
            if tipo == "card":
                if "yellow" in detalle:
                    total_amarillas += 1
                elif "red" in detalle:
                    total_rojas += 1
            elif tipo == "goal" and "penalty" in detalle:
                total_penales += 1

    if analizados == 0:
        stats.resumen = f"Árbitro: {arbitro_nombre} (sin historial en cache)"
        return stats

    stats.partidos_analizados        = analizados
    stats.tarjetas_amarillas_prom    = round(total_amarillas / analizados, 2)
    stats.tarjetas_rojas_prom        = round(total_rojas     / analizados, 2)
    stats.penales_prom               = round(total_penales   / analizados, 2)

    if stats.tarjetas_amarillas_prom >= 5.0 or stats.tarjetas_rojas_prom >= 0.5:
        stats.estilo = "estricto"
    elif stats.tarjetas_amarillas_prom <= 2.5:
        stats.estilo = "permisivo"

    stats.resumen = (
        f"{arbitro_nombre}: {stats.tarjetas_amarillas_prom}🟨/partido "
        f"{stats.tarjetas_rojas_prom}🟥/partido  "
        f"{stats.penales_prom} pen/partido → {stats.estilo}"
    )
    log.info(f"Árbitro: {stats.resumen}")
    return stats


# ══════════════════════════════════════════
#  DESCANSO Y CONTEXTO DEL PARTIDO
# ══════════════════════════════════════════

@dataclass
class ContextoPartido:
    dias_descanso_local:   int   = 7
    dias_descanso_visita:  int   = 7
    es_eliminatorio:       bool  = False
    local_ya_clasificado:  bool  = False
    visita_ya_clasificado: bool  = False
    bonus_descanso:        float = 0.0
    resumen:               str   = ""


def calcular_contexto(fecha_partido: str,
                      fecha_ultimo_local: str,
                      fecha_ultimo_visita: str,
                      es_eliminatorio: bool = False,
                      local_clasificado: bool = False,
                      visita_clasificada: bool = False) -> ContextoPartido:
    ctx = ContextoPartido(
        es_eliminatorio       = es_eliminatorio,
        local_ya_clasificado  = local_clasificado,
        visita_ya_clasificado = visita_clasificada,
    )

    try:
        fecha_p = datetime.fromisoformat(fecha_partido[:10])
        if fecha_ultimo_local:
            ctx.dias_descanso_local  = (fecha_p - datetime.fromisoformat(fecha_ultimo_local[:10])).days
        if fecha_ultimo_visita:
            ctx.dias_descanso_visita = (fecha_p - datetime.fromisoformat(fecha_ultimo_visita[:10])).days
    except Exception:
        pass

    # Ventaja de descanso para el local
    diff = ctx.dias_descanso_local - ctx.dias_descanso_visita
    if diff >= 2:
        ctx.bonus_descanso = 0.03   # local descansó más
    elif diff <= -2:
        ctx.bonus_descanso = -0.03  # visitante descansó más

    notas = []
    if ctx.dias_descanso_local <= 3:
        notas.append(f"Local solo {ctx.dias_descanso_local}d de descanso ⚠️")
    if ctx.dias_descanso_visita <= 3:
        notas.append(f"Visitante solo {ctx.dias_descanso_visita}d de descanso ⚠️")
    if ctx.local_ya_clasificado:
        notas.append("Local ya clasificado → posible rotación")
    if ctx.visita_ya_clasificado:
        notas.append("Visitante ya clasificado → posible rotación")
    if ctx.es_eliminatorio:
        notas.append("Partido eliminatorio — máxima intensidad")

    ctx.resumen = " | ".join(notas) if notas else f"Descanso normal (L:{ctx.dias_descanso_local}d V:{ctx.dias_descanso_visita}d)"
    return ctx


# ══════════════════════════════════════════
#  CLIMA (opcional, requiere OpenWeatherMap)
# ══════════════════════════════════════════

def obtener_clima(ciudad: str) -> dict:
    """
    Obtiene temperatura y condición meteorológica.
    Requiere OPENWEATHER_KEY en .env (plan gratuito: 1000 calls/día).
    """
    import os
    key = os.getenv("OPENWEATHER_KEY", "")
    if not key or not ciudad:
        return {"disponible": False}

    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": ciudad, "appid": key, "units": "metric", "lang": "es"},
            timeout=5
        )
        r.raise_for_status()
        d = r.json()
        return {
            "disponible":   True,
            "ciudad":       ciudad,
            "temp_c":       d["main"]["temp"],
            "descripcion":  d["weather"][0]["description"],
            "lluvia":       "rain" in d.get("weather", [{}])[0].get("main", "").lower(),
            "viento_kmh":   round(d["wind"]["speed"] * 3.6, 1),
        }
    except Exception as e:
        log.warning(f"Clima no disponible para {ciudad}: {e}")
        return {"disponible": False}


# ══════════════════════════════════════════
#  RESUMEN PARA PROMPT
# ══════════════════════════════════════════

def resumen_contexto_para_prompt(forma_l: FormaEquipo, forma_v: FormaEquipo,
                                  h2h: H2H, arbitro: EstadisticasArbitro,
                                  ctx: ContextoPartido, clima: dict) -> str:
    lineas = ["CONTEXTO ADICIONAL:"]

    lineas.append(f"  Forma {forma_l.equipo_nombre}: {forma_l.forma_str or 'N/A'} "
                  f"({forma_l.goles_favor:.1f} GF / {forma_l.goles_contra:.1f} GC por partido)")
    lineas.append(f"  Forma {forma_v.equipo_nombre}: {forma_v.forma_str or 'N/A'} "
                  f"({forma_v.goles_favor:.1f} GF / {forma_v.goles_contra:.1f} GC por partido)")

    if h2h.partidos_totales > 0:
        lineas.append(f"  H2H: {h2h.resumen} | Domina: {h2h.domina}")

    if arbitro.nombre:
        lineas.append(f"  Árbitro: {arbitro.resumen}")

    lineas.append(f"  Descanso: {ctx.resumen}")

    if clima.get("disponible"):
        lineas.append(f"  Clima: {clima['temp_c']}°C, {clima['descripcion']}, "
                      f"viento {clima['viento_kmh']} km/h"
                      + (" ☔ Lluvia" if clima['lluvia'] else ""))

    return "\n".join(lineas)

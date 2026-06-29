# ============================================================
#  player_analyzer.py — Análisis de plantilla y estado físico
#
#  Usa API-Football para obtener:
#    - Alineaciones confirmadas (1-2h antes del partido)
#    - Jugadores lesionados / suspendidos
#    - Minutos jugados recientes (detección de fatiga)
#    - Rotaciones detectadas vs alineación habitual
# ============================================================

import logging
from dataclasses import dataclass, field

import requests

from config import API_FOOTBALL_KEY, API_FOOTBALL_BASE, LOG_FILE

log = logging.getLogger("PlayerAnalyzer")

HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}


@dataclass
class EstadoPlantilla:
    equipo_id:   int
    equipo_nombre: str
    tiene_lineup: bool = False          # ¿hay alineación confirmada?

    titulares:   list[dict] = field(default_factory=list)
    suplentes:   list[dict] = field(default_factory=list)
    lesionados:  list[dict] = field(default_factory=list)
    suspendidos: list[dict] = field(default_factory=list)

    # Flags de impacto
    falta_delantero_titular: bool  = False
    falta_portero_titular:   bool  = False
    falta_defensa_titular:   bool  = False
    hay_rotacion:            bool  = False  # más de 3 cambios vs alineación habitual

    # Fatiga
    jugadores_sobrecargados: list[str] = field(default_factory=list)  # >270 min en 3 semanas

    # Score de impacto (-1.0 a 0.0, siempre negativo o neutro)
    penalizacion: float = 0.0
    resumen:      str   = ""


def _get(endpoint: str, params: dict) -> list:
    try:
        r = requests.get(f"{API_FOOTBALL_BASE}/{endpoint}",
                         headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            log.warning(f"API error en {endpoint}: {data['errors']}")
            return []
        return data.get("response", [])
    except Exception as e:
        log.error(f"Error en {endpoint}: {e}")
        return []


def obtener_estado_plantilla(fixture_id: int,
                             equipo_id: int,
                             equipo_nombre: str,
                             fixture_ids_recientes: list[int]) -> EstadoPlantilla:
    """
    Analiza el estado de la plantilla para un partido concreto.

    fixture_id: ID del partido próximo
    equipo_id: ID del equipo
    fixture_ids_recientes: IDs de los últimos 3-5 partidos del equipo (para calcular fatiga)
    """
    estado = EstadoPlantilla(equipo_id=equipo_id, equipo_nombre=equipo_nombre)

    # ── 1. Alineación confirmada ──────────────────────────
    lineups = _get("fixtures/lineups", {"fixture": fixture_id})
    for lineup in lineups:
        if lineup.get("team", {}).get("id") == equipo_id:
            estado.tiene_lineup = True
            estado.titulares  = lineup.get("startXI", [])
            estado.suplentes  = lineup.get("substitutes", [])

            # Detectar posiciones de titulares
            posiciones_titulares = set()
            for p in estado.titulares:
                pos = p.get("player", {}).get("pos", "").upper()
                posiciones_titulares.add(pos)

            # Si no hay delantero centro ni extremos
            if not any(p in posiciones_titulares for p in ["F", "FW", "ST", "CF"]):
                estado.falta_delantero_titular = True
            if "G" not in posiciones_titulares and "GK" not in posiciones_titulares:
                estado.falta_portero_titular = True
            break

    # ── 2. Lesiones y suspensiones ────────────────────────
    injuries = _get("injuries", {"fixture": fixture_id})
    for inj in injuries:
        if inj.get("team", {}).get("id") != equipo_id:
            continue
        jugador = inj.get("player", {})
        tipo    = inj.get("type", "").lower()
        razon   = inj.get("reason", "").lower()

        info = {
            "nombre": jugador.get("name", ""),
            "tipo":   tipo,
            "razon":  razon,
        }

        if "suspension" in tipo or "yellow card" in razon or "red card" in razon:
            estado.suspendidos.append(info)
        else:
            estado.lesionados.append(info)

        # Detectar si es titular clave según posición
        pos = jugador.get("position", "").lower()
        if any(p in pos for p in ["forward", "attacker", "striker"]):
            estado.falta_delantero_titular = True
        if "goalkeeper" in pos:
            estado.falta_portero_titular = True
        if "defender" in pos:
            estado.falta_defensa_titular = True

    # ── 3. Fatiga — minutos acumulados recientes ──────────
    minutos_por_jugador: dict[str, int] = {}
    for fix_id in fixture_ids_recientes[-3:]:  # últimos 3 partidos
        stats = _get("fixtures/players", {"fixture": fix_id})
        for team_data in stats:
            if team_data.get("team", {}).get("id") != equipo_id:
                continue
            for p in team_data.get("players", []):
                nombre = p.get("player", {}).get("name", "")
                mins   = p.get("statistics", [{}])[0].get("games", {}).get("minutes", 0) or 0
                minutos_por_jugador[nombre] = minutos_por_jugador.get(nombre, 0) + mins

    for nombre, mins in minutos_por_jugador.items():
        if mins >= 270:  # 90 min × 3 partidos = máxima carga
            estado.jugadores_sobrecargados.append(f"{nombre} ({mins}min/3j)")

    # ── 4. Calcular penalización ──────────────────────────
    pen = 0.0
    detalles = []

    if estado.falta_portero_titular:
        pen -= 0.08
        detalles.append("Portero titular no juega (-8%)")
    if estado.falta_delantero_titular:
        pen -= 0.06
        detalles.append("Delantero titular no juega (-6%)")
    if estado.falta_defensa_titular:
        pen -= 0.04
        detalles.append("Defensa clave no juega (-4%)")

    pen -= len(estado.suspendidos) * 0.03
    if estado.suspendidos:
        detalles.append(f"{len(estado.suspendidos)} suspensión(es) (-{len(estado.suspendidos)*3}%)")

    pen -= len(estado.lesionados) * 0.02
    if estado.lesionados:
        detalles.append(f"{len(estado.lesionados)} lesión(es) (-{len(estado.lesionados)*2}%)")

    if len(estado.jugadores_sobrecargados) >= 3:
        pen -= 0.04
        detalles.append(f"Fatiga acumulada ({len(estado.jugadores_sobrecargados)} jugadores) (-4%)")

    estado.penalizacion = max(-0.25, pen)  # cap en -25%
    estado.resumen = " | ".join(detalles) if detalles else "Plantilla completa"

    log.info(f"{equipo_nombre}: pen={estado.penalizacion:.0%} | {estado.resumen}")
    return estado


def resumen_para_prompt(estado: EstadoPlantilla) -> str:
    """Devuelve texto compacto para incluir en el prompt de OpenAI."""
    if not estado.tiene_lineup:
        lineas = [f"  {estado.equipo_nombre}: alineación no confirmada aún"]
    else:
        lineas = [f"  {estado.equipo_nombre}:"]

    if estado.lesionados:
        lineas.append(f"    ⛔ Lesionados: {', '.join(j['nombre'] for j in estado.lesionados[:4])}")
    if estado.suspendidos:
        lineas.append(f"    🟨 Suspendidos: {', '.join(j['nombre'] for j in estado.suspendidos[:4])}")
    if estado.falta_portero_titular:
        lineas.append(f"    🧤 PORTERO TITULAR NO JUEGA")
    if estado.falta_delantero_titular:
        lineas.append(f"    ⚡ DELANTERO TITULAR NO JUEGA")
    if estado.jugadores_sobrecargados:
        lineas.append(f"    😓 Sobrecarga: {', '.join(estado.jugadores_sobrecargados[:3])}")
    if not (estado.lesionados or estado.suspendidos or estado.jugadores_sobrecargados):
        lineas.append("    ✅ Sin bajas conocidas")

    lineas.append(f"    → Penalización: {estado.penalizacion:.0%}")
    return "\n".join(lineas)

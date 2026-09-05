# ============================================================
#  analizar_partido.py — Análisis bajo demanda de UN partido
#
#  Creado por Diego Aleman.
#
#  A diferencia de sharp_live.py (corre solo, cada 12h, sobre 6 ligas
#  fijas), esto es interactivo: elegís un partido de la lista y te
#  muestra, lado a lado, TODO lo que hay para decidir —
#    - Cuotas reales de 1xbet y Ecuabet (las dos casas donde jugás,
#      no un intermediario ni un "no-vig" de Pinnacle)
#    - Forma reciente + posición en la tabla + H2H (Sofascore)
#    - Historial de posesión/goles esperados/tiros/corners/tarjetas
#      de los últimos 5 partidos de cada equipo (Sofascore) — para
#      mercados que sharp_ev.py y combo_builder.py no cubren (esos
#      son solo 1X2 y goles)
#
#  Filosofía: NO decide por vos. Te muestra el precio + la frecuencia
#  histórica una al lado de la otra y salís vos con el criterio —
#  mismo espíritu que combo_builder.py (preferir el menú completo a
#  una sola recomendación empaquetada).
#
#  Este módulo solo junta y estructura los datos (funciones get_*) —
#  tanto la versión de consola (al final de este archivo) como el
#  dashboard (dashboard_partido.py) consumen las mismas funciones,
#  para no duplicar la lógica de fetch/parseo en dos lugares.
#
#  Límite conocido de los mercados de 1xbet: vienen con códigos
#  numéricos sin nombre. Los que están en MERCADOS_1XBET_CONOCIDOS
#  se confirmaron a mano comparando el patrón de precios (ver
#  comentario junto a esa tabla); el resto se muestra como "sin
#  identificar" en vez de arriesgar una etiqueta inventada. Ecuabet
#  nombra sus propios mercados, pero su API pública (el widget, no la
#  web completa) solo expone un subconjunto acotado — lo mismo pasa
#  incluso pidiendo el detalle de un partido puntual (GetEventsById):
#  no hay forma encontrada todavía de sacarle más profundidad (ej.
#  corners/tarjetas) sin iniciar sesión real en el sitio.
#
#  Uso:
#    python analizar_partido.py
# ============================================================

from datetime import datetime, timezone

import requests

import arb_scanner
import sofascore_client as sc

HEADERS_1XBET = {**arb_scanner.HEADERS_BASE, "Referer": "https://1xbet.ec/es/line/football"}
HEADERS_ECUABET = {**arb_scanner.HEADERS_BASE, "Referer": "https://ecuabet.com/"}

N_HISTORIAL = 5          # partidos hacia atrás para forma + stats
PAGINAS_ECUABET = 40     # cobertura completa (~1850 partidos) — si no,
                         # la mayoría de los partidos de 1xbet no cruzan
                         # con Ecuabet (verificado: con 6 páginas solo
                         # cruzaba 1/50, con todas las páginas 16/50 —
                         # el resto de verdad no está en Ecuabet ahora)

# Mercados de 1xbet decodificados a mano comparando patrón de precios
# entre varios partidos reales (ver conversación de construcción):
#   - groupId 1  (1X2): types 1/2/3 = local/empate/visitante — confirmado
#     por estructura (3 outcomes que suman ~1 de prob. implícita).
#   - groupId 17 (goles O/U): types 9/10 = Over/Under — confirmado: cf de
#     type9 SUBE con la línea (correcto para "Over", raro ganar de menos)
#     y cf de type10 BAJA con la línea (correcto para "Under").
#   - groupId 8  (doble oportunidad): types 4/5/6 = 1X/12/X2 — orden
#     estándar de la industria, NO verificado 1 a 1 con otra fuente.
#   - groupId 19 (ambos anotan): types 180/181 = Sí/No — orden supuesto,
#     no verificado.
#   - groupId 2  (hándicap asiático): types 7/8 = local/visitante,
#     "parameter" = hándicap de ESE lado — confirmado por patrón (type7
#     con parameter negativo grande da cf muy caro, correcto para "gana
#     por 2+" en un handicap a favor del rival).
MERCADOS_1XBET_CONOCIDOS = {
    1:  ("1X2",                {1: "Local", 2: "Empate", 3: "Visitante"}),
    17: ("Goles Over/Under",   {9: "Over", 10: "Under"}),
    8:  ("Doble oportunidad*", {4: "1X", 5: "12", 6: "X2"}),
    19: ("Ambos anotan*",      {180: "Sí", 181: "No"}),
    2:  ("Hándicap asiático",  {7: "Local", 8: "Visitante"}),
}
# (*) orden de opciones no verificado contra una segunda fuente — mirar
# la cuota antes de confiar ciegamente en cuál lado es cuál.


# ────────────────────────────────────────────────────────────
#  Carga de datos crudos (no reducidos a solo 1X2, para poder
#  mostrar los demás mercados y los stats más adelante)
# ────────────────────────────────────────────────────────────

def _cargar_1xbet_raw(count: int = 50) -> list[dict]:
    url = "https://1xbet.ec/service-api/main-line-feed/v3/games1x2"
    params = {"cfView": 3, "count": count, "country": 209, "fcountry": 209,
              "gr": 285, "grMode": 4, "lng": "es", "ref": 1}
    r = requests.get(url, params=params, headers=HEADERS_1XBET, timeout=20)
    r.raise_for_status()
    return r.json()


def _cargar_ecuabet_raw(paginas: int = PAGINAS_ECUABET, eventos_por_pagina: int = 50) -> dict:
    """Devuelve {events, markets, odds, competidores} ya fusionados de
    varias páginas de GetUpcoming. Con cobertura completa (~1850
    partidos) esto tarda ~30s — se paga una sola vez al arrancar."""
    events, markets_por_id, odds_por_id, competidores = [], {}, {}, {}
    for page in range(1, paginas + 1):
        url = "https://sb2frontend-altenar2.biahosted.com/api/widget/GetUpcoming"
        params = {
            "culture": "es-ES", "timezoneOffset": 300, "integration": "ecuabet",
            "deviceType": 1, "numFormat": "en-GB", "countryCode": "EC",
            "eventCount": eventos_por_pagina, "sportId": 66, "page": page,
        }
        r = requests.get(url, params=params, headers=HEADERS_ECUABET, timeout=20)
        r.raise_for_status()
        data = r.json()
        events.extend(data.get("events", []))
        markets_por_id.update({m["id"]: m for m in data.get("markets", [])})
        odds_por_id.update({o["id"]: o for o in data.get("odds", [])})
        competidores.update({c["id"]: c["name"] for c in data.get("competitors", [])})
        if page >= data.get("pageCount", page):
            break
    return {"events": events, "markets": markets_por_id, "odds": odds_por_id, "competidores": competidores}


# ────────────────────────────────────────────────────────────
#  Listado de partidos
# ────────────────────────────────────────────────────────────

def get_partidos() -> list[dict]:
    """Un registro por partido de 1xbet (su top ~50 por popularidad),
    con el partido equivalente de Ecuabet adjunto cuando existe.
    Ecuabet tiene ~1850 partidos —demasiadas ligas chicas para listar
    todas—, así que la lista maestra es la de 1xbet (ya viene filtrada
    a lo más relevante) y Ecuabet se suma como segunda cotización."""
    crudos_1x = _cargar_1xbet_raw()
    ecuabet = _cargar_ecuabet_raw()

    partidos = []
    for g in crudos_1x:
        home = g.get("opponent1", {}).get("fullName", "")
        away = g.get("opponent2", {}).get("fullName", "")
        ec_match = None
        for e in ecuabet["events"]:
            comp_ids = e.get("competitorIds", [])
            if len(comp_ids) != 2:
                continue
            eh = ecuabet["competidores"].get(comp_ids[0], "")
            ea = ecuabet["competidores"].get(comp_ids[1], "")
            if arb_scanner._mismo_equipo(eh, home) and arb_scanner._mismo_equipo(ea, away):
                ec_match = e
                break
        partidos.append({
            "home": home, "away": away, "liga": g.get("liga", {}).get("name", ""),
            "start_ts": g.get("startTs", 0), "raw_1x": g, "raw_ec": ec_match,
            "ecuabet_ctx": ecuabet,
        })
    return partidos


# ────────────────────────────────────────────────────────────
#  Cuotas del partido elegido
# ────────────────────────────────────────────────────────────

def get_mercados_1xbet(g: dict) -> dict:
    """{"1X2": {...}, "Goles Over/Under": [...], ..., "_sin_identificar": [ids]}"""
    grupos = {eg["groupId"]: eg for eg in g.get("eventGroups", [])}
    resultado = {}

    for group_id, (nombre_mercado, mapa_types) in MERCADOS_1XBET_CONOCIDOS.items():
        grupo = grupos.get(group_id)
        if not grupo:
            continue
        # agrupar por línea/parameter (None si el mercado no tiene líneas, ej. 1X2)
        por_linea: dict = {}
        for slot in grupo["events"]:
            for oc in slot:
                nombre_opcion = mapa_types.get(oc["type"])
                if nombre_opcion is None:
                    continue
                por_linea.setdefault(oc.get("parameter"), {})[nombre_opcion] = oc["cf"]

        if list(por_linea.keys()) == [None]:
            resultado[nombre_mercado] = por_linea[None]
        else:
            resultado[nombre_mercado] = [
                {"linea": linea, **opciones} for linea, opciones in sorted(
                    por_linea.items(), key=lambda kv: (kv[0] is None, kv[0]))
            ]

    identificados = set(MERCADOS_1XBET_CONOCIDOS)
    resultado["_sin_identificar"] = [eg["groupId"] for eg in g.get("eventGroups", [])
                                     if eg["groupId"] not in identificados]
    return resultado


def get_mercados_ecuabet(ev: dict, ecuabet: dict) -> list[dict]:
    """[{"mercado": str, "opciones": {nombre: cuota, ...}}, ...]"""
    if not ev:
        return []
    salida = []
    for mid in ev.get("marketIds", []):
        m = ecuabet["markets"].get(mid)
        if not m:
            continue
        opciones = {o["name"]: o["price"] for oid in m["oddIds"]
                    if (o := ecuabet["odds"].get(oid))}
        salida.append({"mercado": m.get("name", "?"), "opciones": opciones})
    return salida


# ────────────────────────────────────────────────────────────
#  Estadísticas de Sofascore
# ────────────────────────────────────────────────────────────

def get_stats_equipo(nombre: str) -> dict:
    """{"equipo_id", "forma", "posicion", "historial"} — cualquiera de
    estos puede venir None/[] si Sofascore no tiene el dato."""
    equipo_id = sc.buscar_equipo_id(nombre)
    if not equipo_id:
        return {"equipo_id": None, "forma": None, "posicion": None, "historial": []}

    return {
        "equipo_id": equipo_id,
        "forma": sc.forma_equipo(equipo_id, nombre, n=N_HISTORIAL),
        "posicion": sc.posicion_equipo(equipo_id),
        "historial": sc.historial_stats_equipo(equipo_id, n=N_HISTORIAL),
    }


def get_h2h(home_nombre: str, away_nombre: str, start_ts: int) -> "sc.H2H | None":
    home_id = sc.buscar_equipo_id(home_nombre)
    if not home_id:
        return None
    commence_iso = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
    evento = sc.buscar_evento_proximo(home_id, away_nombre, commence_iso)
    if not evento:
        return None
    return sc.h2h_evento(evento["id"], home_nombre, away_nombre)


# ────────────────────────────────────────────────────────────
#  Versión de consola
# ────────────────────────────────────────────────────────────

def _elegir_partido(partidos: list[dict]) -> dict:
    print(f"\n{len(partidos)} partidos disponibles (1xbet + Ecuabet donde coincide):\n")
    for i, p in enumerate(partidos, 1):
        casas = "1xbet" + (" + Ecuabet" if p["raw_ec"] else "")
        print(f"  {i:2d}. {p['home']} vs {p['away']}  ({p['liga']})  [{casas}]")
    while True:
        try:
            idx = int(input("\nElegí un partido (número): ").strip()) - 1
            if 0 <= idx < len(partidos):
                return partidos[idx]
        except ValueError:
            pass
        print("Número inválido, probá de nuevo.")


def _imprimir_mercados_1xbet(mercados: dict) -> None:
    for nombre, contenido in mercados.items():
        if nombre == "_sin_identificar":
            continue
        if isinstance(contenido, dict):
            print(f"    {nombre}: " + "  ".join(f"{k} {v}" for k, v in contenido.items()))
        else:
            print(f"    {nombre}:")
            for fila in contenido:
                linea = fila["linea"]
                resto = "  ".join(f"{k} {v}" for k, v in fila.items() if k != "linea")
                print(f"      {linea}:  {resto}")
    if mercados.get("_sin_identificar"):
        print(f"    (+{len(mercados['_sin_identificar'])} mercado(s) más sin identificar — "
              f"códigos: {mercados['_sin_identificar']})")


def _imprimir_stats_equipo(nombre: str, stats: dict) -> None:
    if not stats["equipo_id"]:
        print(f"    {nombre}: no encontrado en Sofascore")
        return

    forma = stats["forma"]
    print(f"    {nombre} — forma: {forma.forma_str or 'N/A'} "
          f"({forma.goles_favor:.1f} GF / {forma.goles_contra:.1f} GC por partido, {forma.partidos} partidos)")

    pos = stats["posicion"]
    if pos:
        print(f"      Tabla: {pos['posicion']}°/{pos['total_equipos']}  "
              f"{pos['puntos']} pts  ({pos['ganados']}G-{pos['empatados']}E-{pos['perdidos']}P en {pos['jugados']})")

    for p in stats["historial"]:
        af, ec = p["a_favor"], p["en_contra"]
        corners_txt = (f"corners {af.get('corners')}-{ec.get('corners')}"
                       if af.get("corners") is not None else "corners ?")
        print(f"      vs {p['rival']:<24s} {corners_txt}   amarillas {af.get('amarillas')}   "
              f"tiros {af.get('tiros_totales')}   posesión {af.get('posesion')}%")


def main() -> None:
    print(f"Cargando partidos de 1xbet y Ecuabet (cobertura completa, puede tardar ~30s)...")
    partidos = get_partidos()
    elegido = _elegir_partido(partidos)

    print(f"\n=== {elegido['home']} vs {elegido['away']} ({elegido['liga']}) ===\n")

    print("CUOTAS — 1xbet:")
    _imprimir_mercados_1xbet(get_mercados_1xbet(elegido["raw_1x"]))

    print("\nCUOTAS — Ecuabet:")
    mercados_ec = get_mercados_ecuabet(elegido["raw_ec"], elegido["ecuabet_ctx"])
    if mercados_ec:
        for m in mercados_ec:
            texto = "  ".join(f"{k} {v}" for k, v in m["opciones"].items())
            print(f"    {m['mercado']}: {texto}")
    else:
        print("    (Ecuabet no tiene este partido en su listado de próximos)")

    print(f"\nESTADÍSTICAS (Sofascore, últimos {N_HISTORIAL} partidos)")
    _imprimir_stats_equipo(elegido["home"], get_stats_equipo(elegido["home"]))
    _imprimir_stats_equipo(elegido["away"], get_stats_equipo(elegido["away"]))

    print("\nH2H:")
    h2h = get_h2h(elegido["home"], elegido["away"], elegido["start_ts"])
    if h2h:
        print(f"    {h2h.resumen} | Domina: {h2h.domina}")
    else:
        print("    Partido no encontrado en el calendario de Sofascore para el H2H")


if __name__ == "__main__":
    main()

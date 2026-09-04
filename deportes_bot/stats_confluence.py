# ============================================================
#  stats_confluence.py — Segunda mirada estadística sobre las
#  patas "seguras" de combo_builder.py
#
#  Creado por Diego Aleman.
#
#  combo_builder.py decide qué patas son "seguras" mirando SOLO el
#  precio de mercado (no-vig Pinnacle) — que es fuerte, pero no ve
#  nada de lo que pasó en la cancha. Este módulo agrega una segunda
#  fuente, independiente del precio: forma reciente y head-to-head
#  (API-Football), justo para el caso que el precio solo no puede
#  ver — un favorito claro para el mercado que en la práctica viene
#  de racha floja (lesiones, rotación, mala forma no reflejada aún
#  en la cuota, dinero público empujando el precio).
#
#  Filosofía deliberada: esto NO mejora la probabilidad de una pata
#  (no hay forma confiable de combinar ambas fuentes sin más datos
#  y sin overfitear). Solo puede DEGRADARLA: si la estadística
#  CONTRADICE claramente al mercado, la pata se descarta de las
#  combinadas. Si no se puede verificar (equipo no encontrado, sin
#  API_FOOTBALL_KEY, forma insuficiente) queda neutral — ni confirma
#  ni descarta, la pata sigue valiendo solo por precio.
#
#  Costo en cuota de API-Football: 1 descarga de ventana de fechas
#  por ciclo (~7-8 requests, ya la hace data_collector.py) + como
#  mucho 1 request de H2H por pata candidata (solo si la forma no
#  contradijo ya). Con 2 ciclos/día esto son ~15-25 requests/día
#  extra — cabe cómodo en las 100/día del plan free.
# ============================================================

import re
import unicodedata
from dataclasses import dataclass

import stats_analyzer

RATIO_FORMA_MINIMO = 0.35     # menos de esto de puntos posibles = forma floja
MIN_PARTIDOS_FORMA = 3        # con menos partidos jugados no se confía en el ratio
MIN_H2H_PARTIDOS   = 4        # con menos H2H no se confía en el dominio


@dataclass
class Confluencia:
    estado:  str   # "confirma" | "contradice" | "sin_datos"
    detalle: str


def _normalizar(nombre: str) -> str:
    n = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    n = n.lower()
    for suf in (" fc", " cf", " sc", " afc", " ac", "fc ", "cf "):
        n = n.replace(suf, " ")
    return re.sub(r"\s+", " ", n).strip()


def _mismo_equipo(a: str, b: str) -> bool:
    na, nb = _normalizar(a), _normalizar(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _buscar_fixture(pata, fixtures_cache: list[dict]) -> dict | None:
    """Empareja una PataSegura (de The Odds API) con su fixture
    equivalente de API-Football, por fecha + nombres de equipo."""
    fecha = pata.commence_time[:10]
    for f in fixtures_cache:
        if f.get("fixture", {}).get("date", "")[:10] != fecha:
            continue
        h = f.get("teams", {}).get("home", {}).get("name", "")
        a = f.get("teams", {}).get("away", {}).get("name", "")
        if _mismo_equipo(h, pata.home) and _mismo_equipo(a, pata.away):
            return f
    return None


def evaluar(pata, fixtures_cache: list[dict]) -> Confluencia:
    """Evalúa UNA pata segura contra forma reciente + H2H de API-Football.
    "sin_datos" es el resultado por defecto ante cualquier falta de
    información — nunca se inventa una contradicción sin evidencia."""
    if pata.outcome == "empate":
        return Confluencia("sin_datos", "Empate: la forma no se evalúa por outcome")

    fx = _buscar_fixture(pata, fixtures_cache)
    if not fx:
        return Confluencia("sin_datos", "Partido no encontrado en API-Football")

    liga_id = fx.get("league", {}).get("id")
    season  = str(fx.get("league", {}).get("season", ""))
    lado    = "home" if pata.outcome == "local" else "away"
    otro    = "away" if lado == "home" else "home"
    equipo    = fx["teams"][lado]
    contrario = fx["teams"][otro]

    forma     = stats_analyzer.obtener_forma(equipo["id"], equipo["name"], liga_id, season, fixtures_cache)
    forma_riv = stats_analyzer.obtener_forma(contrario["id"], contrario["name"], liga_id, season, fixtures_cache)

    if forma.partidos < MIN_PARTIDOS_FORMA:
        return Confluencia("sin_datos", f"Forma insuficiente ({forma.partidos} partidos en cache)")

    ratio     = forma.puntos_forma / (forma.partidos * 3)
    ratio_riv = forma_riv.puntos_forma / (forma_riv.partidos * 3) if forma_riv.partidos else 0.5

    if ratio < RATIO_FORMA_MINIMO and ratio <= ratio_riv:
        return Confluencia(
            "contradice",
            f"{equipo['name']} favorito del mercado pero forma floja "
            f"({forma.forma_str}, {ratio:.0%} de puntos posibles)")

    h2h = stats_analyzer.obtener_h2h(equipo["id"], contrario["id"], equipo["name"], contrario["name"])
    lado_rival_h2h = "visitante" if lado == "home" else "local"
    if h2h.partidos_totales >= MIN_H2H_PARTIDOS and h2h.domina == lado_rival_h2h:
        return Confluencia("contradice", f"H2H desfavorable: {h2h.resumen}")

    return Confluencia("confirma", f"Forma {equipo['name']}: {forma.forma_str or 'N/A'} ({ratio:.0%})")

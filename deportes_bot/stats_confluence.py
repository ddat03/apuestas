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
#  (sofascore_client.py), justo para el caso que el precio solo no
#  puede ver — un favorito claro para el mercado que en la práctica
#  viene de racha floja (lesiones, rotación, mala forma no reflejada
#  aún en la cuota, dinero público empujando el precio).
#
#  Filosofía deliberada: esto NO mejora la probabilidad de una pata
#  (no hay forma confiable de combinar ambas fuentes sin más datos
#  y sin overfitear). Solo puede DEGRADARLA: si la estadística
#  CONTRADICE claramente al mercado, la pata se descarta de las
#  combinadas. Si no se puede verificar (equipo no encontrado, forma
#  insuficiente) queda neutral — ni confirma ni descarta, la pata
#  sigue valiendo solo por precio.
#
#  Antes esto usaba API-Football (cache de fixtures por ventana de
#  fechas + h2h crudo). Se migró a Sofascore el 2026-09-05: la cuenta
#  de API-Football quedó bloqueada, y de paso Sofascore da H2H directo
#  (ya viene resuelto por evento, sin recorrer partido por partido) y
#  no depende de ninguna key ni cuenta que puedan volver a bloquear.
# ============================================================

from dataclasses import dataclass

import sofascore_client

RATIO_FORMA_MINIMO = 0.35     # menos de esto de puntos posibles = forma floja
MIN_PARTIDOS_FORMA = 3        # con menos partidos jugados no se confía en el ratio
MIN_H2H_PARTIDOS   = 4        # con menos H2H no se confía en el dominio


@dataclass
class Confluencia:
    estado:  str   # "confirma" | "contradice" | "sin_datos"
    detalle: str


def evaluar(pata) -> Confluencia:
    """Evalúa UNA pata segura contra forma reciente + H2H de Sofascore.
    "sin_datos" es el resultado por defecto ante cualquier falta de
    información — nunca se inventa una contradicción sin evidencia."""
    if pata.outcome == "empate":
        return Confluencia("sin_datos", "Empate: la forma no se evalúa por outcome")

    home_id = sofascore_client.buscar_equipo_id(pata.home)
    away_id = sofascore_client.buscar_equipo_id(pata.away)
    if not home_id or not away_id:
        return Confluencia("sin_datos", "Equipo no encontrado en Sofascore")

    es_local = pata.outcome == "local"
    equipo_id, equipo_nombre = (home_id, pata.home) if es_local else (away_id, pata.away)
    rival_id, rival_nombre   = (away_id, pata.away) if es_local else (home_id, pata.home)

    forma     = sofascore_client.forma_equipo(equipo_id, equipo_nombre)
    forma_riv = sofascore_client.forma_equipo(rival_id, rival_nombre)

    if forma.partidos < MIN_PARTIDOS_FORMA:
        return Confluencia("sin_datos", f"Forma insuficiente ({forma.partidos} partidos)")

    ratio     = forma.puntos_forma / (forma.partidos * 3)
    ratio_riv = forma_riv.puntos_forma / (forma_riv.partidos * 3) if forma_riv.partidos else 0.5

    if ratio < RATIO_FORMA_MINIMO and ratio <= ratio_riv:
        return Confluencia(
            "contradice",
            f"{equipo_nombre} favorito del mercado pero forma floja "
            f"({forma.forma_str}, {ratio:.0%} de puntos posibles)")

    evento = sofascore_client.buscar_evento_proximo(home_id, pata.away, pata.commence_time)
    if evento:
        h2h = sofascore_client.h2h_evento(evento["id"], pata.home, pata.away)
        lado_rival_h2h = "visitante" if es_local else "local"
        if h2h.partidos_totales >= MIN_H2H_PARTIDOS and h2h.domina == lado_rival_h2h:
            return Confluencia("contradice", f"H2H desfavorable: {h2h.resumen}")

    return Confluencia("confirma", f"Forma {equipo_nombre}: {forma.forma_str or 'N/A'} ({ratio:.0%})")

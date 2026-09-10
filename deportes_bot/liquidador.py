# ============================================================
#  liquidador.py — Liquidación automática de combinadas guardadas
#
#  Creado por Diego Aleman.
#
#  Cuando un partido de una combinada guardada ya terminó, marca la
#  pata ✓ (acierto) / ✗ (fallo) mirando el marcador final en Sofascore
#  (gratis, sin key). Cubre los mercados que arma la pestaña Combinada
#  PrimaTips: 1X2 y doble oportunidad. Otros mercados quedan
#  "pendiente" con una nota — Diego los marca a mano.
#
#  Nunca decide una apuesta: solo verifica un resultado que ya pasó.
# ============================================================

from __future__ import annotations

import re

import primatips_combos as pc
import sofascore_client as sc

# resultado: "acierto" | "fallo" | "anulado" | "pendiente"


def _fecha_de_partido(pata: dict) -> str:
    """El schema de combo_picks no guarda kickoff; se saca de la liga
    solo si viene con fecha, si no None -> resultado_partido igual
    matchea por nombres."""
    return pata.get("fecha") or ""


def _parsear_mercado(mercado: str) -> tuple[str, str]:
    """mercado guardado por la pestaña Combinada: 'PrimaTips · 1x2 · 1'
    o 'PrimaTips · Doble oportunidad · X2'. Devuelve (tipo, codigo)."""
    partes = [p.strip() for p in mercado.split("·")]
    codigo = partes[-1].upper() if len(partes) >= 3 else ""
    m = mercado.lower()
    if "doble oportunidad" in m or "double chance" in m:
        return "doble", codigo
    if "1x2" in m or "1 x 2" in m:
        return "1x2", codigo
    if "sin empate" in m or "draw no bet" in m:
        return "dnb", codigo
    return "otro", codigo


def _resolver_1x2(codigo: str, gh: int, ga: int) -> str:
    gana = "1" if gh > ga else ("2" if ga > gh else "X")
    return "acierto" if codigo == gana else "fallo"


def _resolver_doble(codigo: str, gh: int, ga: int) -> str:
    gana = "1" if gh > ga else ("2" if ga > gh else "X")
    ok = {"1X": {"1", "X"}, "12": {"1", "2"}, "X2": {"X", "2"}}.get(codigo, set())
    return "acierto" if gana in ok else "fallo"


def _resolver_dnb(codigo: str, gh: int, ga: int) -> str:
    gana = "1" if gh > ga else ("2" if ga > gh else "X")
    if gana == "X":
        return "anulado"   # empate = stake devuelto
    return "acierto" if codigo == gana else "fallo"


def liquidar_pata(pata: dict) -> tuple[str, str]:
    """pata: dict de combo_picks (partido='Home vs Away', mercado, seleccion, ...).
    Devuelve (resultado, detalle)."""
    partido = pata.get("partido", "")
    m = re.split(r"\s+vs\s+", partido, maxsplit=1, flags=re.I)
    if len(m) != 2:
        return "pendiente", f"no pude separar el partido de «{partido}»"
    home, away = m[0].strip(), m[1].strip()

    tipo, codigo = _parsear_mercado(pata.get("mercado", ""))
    if tipo == "otro" or not codigo:
        return "pendiente", "mercado no auto-liquidable (marcalo a mano)"

    res = sc.resultado_partido(home, away, _fecha_de_partido(pata), matcher=pc._mismo_equipo)
    if not res:
        return "pendiente", "partido no encontrado en Sofascore todavía"
    if res["status"] != "finished":
        return "pendiente", f"partido {res['status']} (aún no terminó)"

    gh, ga = res.get("gh"), res.get("ga")
    if gh is None or ga is None:
        return "pendiente", "sin marcador final todavía"

    if tipo == "1x2":
        r = _resolver_1x2(codigo, gh, ga)
    elif tipo == "doble":
        r = _resolver_doble(codigo, gh, ga)
    elif tipo == "dnb":
        r = _resolver_dnb(codigo, gh, ga)
    else:
        return "pendiente", "mercado no auto-liquidable"

    return r, f"final {home} {gh}-{ga} {away}"


def liquidar_combinadas(listar_combinadas, marcar_pata_resultado, solo_pendientes: bool = True) -> dict:
    """Recorre las combinadas y liquida las patas pendientes.
    `listar_combinadas` y `marcar_pata_resultado` se inyectan desde
    combos_manual_db para no acoplar módulos.
    Devuelve un contador {revisadas, aciertos, fallos, anulados, pendientes}."""
    cont = {"revisadas": 0, "acierto": 0, "fallo": 0, "anulado": 0, "pendiente": 0}
    for combo in listar_combinadas():
        for pata in combo.get("patas", []):
            if solo_pendientes and pata.get("resultado", "pendiente") != "pendiente":
                continue
            cont["revisadas"] += 1
            resultado, detalle = liquidar_pata(pata)
            cont[resultado] = cont.get(resultado, 0) + 1
            if resultado != "pendiente":
                marcar_pata_resultado(pata["id"], resultado, detalle)
    return cont

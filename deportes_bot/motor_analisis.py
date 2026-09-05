# ============================================================
#  motor_analisis.py — Veredicto de una selección puntual
#
#  Creado por Diego Aleman.
#
#  Cruza UNA selección (ej. "Newcastle Más de 2.5 goles") contra la
#  frecuencia histórica real (Sofascore, últimos N partidos de cada
#  equipo) y la cuota ofrecida. NO es un modelo — es aritmética
#  simple y transparente: cuenta cuántos de los últimos partidos
#  hubieran cumplido la línea, y compara esa frecuencia contra la
#  probabilidad implícita de la cuota (1/cuota).
#
#  Si la selección no encaja en ningún mercado que sepamos cruzar
#  (ej. hándicaps, algo específico de una casa), el veredicto es
#  honesto: "sin datos para analizar esto" — nunca se inventa un
#  análisis para quedar bien.
#
#  También suma dos señales cualitativas que la sola frecuencia no
#  ve: ausencias/lesiones del partido puntual, y si la muestra es
#  chica (mismo espíritu que stats_confluence.py: no ocultar cuando
#  la base es débil).
# ============================================================

import re
import unicodedata
from dataclasses import dataclass

MIN_PARTIDOS_CONFIABLE = 4   # con menos, se avisa que la muestra es chica


@dataclass
class Veredicto:
    analizable: bool
    resumen: str
    detalle: str = ""


def _parsear_linea(seleccion: str, extra_linea: float | None) -> tuple[str, float] | None:
    """Detecta 'Over/Under X' o 'Más de X'/'Menos de X' en el texto de
    la selección, o usa `extra_linea` si ya viene separada (mercados
    de 1xbet la traen aparte, no en el texto)."""
    texto = seleccion.lower()
    if extra_linea is not None:
        if "over" in texto or "más de" in texto or "mas de" in texto:
            return ("over", extra_linea)
        if "under" in texto or "menos de" in texto:
            return ("under", extra_linea)
    m = re.search(r"(más de|mas de|menos de|over|under)\s*([\d.]+)", texto)
    if not m:
        return None
    lado = "over" if m.group(1) in ("más de", "mas de", "over") else "under"
    return (lado, float(m.group(2)))


def _frecuencia(valores: list[float], lado: str, linea: float) -> tuple[int, int]:
    cumplen = sum(1 for v in valores if v is not None and ((v > linea) if lado == "over" else (v < linea)))
    validos = sum(1 for v in valores if v is not None)
    return cumplen, validos


def _historiales_segun_alcance(alcance: str, hist_home: list[dict], hist_away: list[dict]) -> list[dict]:
    """alcance: "total" (partido completo, ambos equipos) | "home" | "away"
    (la apuesta es sobre UN equipo puntual, ej. "Newcastle más de 5.5
    corners" — ahí solo importa el historial de Newcastle, no el rival)."""
    if alcance == "home":
        return hist_home
    if alcance == "away":
        return hist_away
    return hist_home + hist_away


def _totales_por_partido(historiales: list[dict], extractor) -> list[float]:
    """extractor(h) -> valor de ESE partido (o None si falta el dato)."""
    valores = []
    for h in historiales:
        v = extractor(h)
        if v is not None:
            valores.append(v)
    return valores


def _analizar_stat_total(seleccion: str, cuota: float, extra_linea: float | None,
                         historiales: list[dict], extractor, etiqueta: str, alcance: str) -> Veredicto | None:
    """Genérico para cualquier mercado de "Más/Menos de X <stat>" —
    lo usan goles, corners y tarjetas, ya sea total del partido o de
    un equipo puntual (ver _historiales_segun_alcance)."""
    par = _parsear_linea(seleccion, extra_linea)
    if not par:
        return None
    lado, linea = par

    totales = _totales_por_partido(historiales, extractor)
    cumplen, validos = _frecuencia(totales, lado, linea)
    if validos < MIN_PARTIDOS_CONFIABLE:
        return Veredicto(True, f"Muestra chica ({validos} partidos con datos) — no confiar solo en esto",
                         f"{cumplen}/{validos} partidos habrían cumplido '{lado} {linea} {etiqueta}'")

    freq = cumplen / validos
    prob_implicita = 1.0 / cuota
    diferencia = freq - prob_implicita
    veredicto = ("la frecuencia histórica supera bastante lo que paga la cuota — revisar en vivo"
                if diferencia > 0.12 else
                "la cuota ya parece reflejar bien la frecuencia histórica"
                if abs(diferencia) <= 0.12 else
                "la frecuencia histórica es MENOR a lo que la cuota sugiere — cuidado")
    contexto_txt = ("total del partido, ambos equipos" if alcance == "total" else
                    f"solo del equipo elegido ({'local' if alcance == 'home' else 'visita'})")
    return Veredicto(True,
                     f"{cumplen}/{validos} partidos recientes ({freq:.0%}) cumplieron '{lado} {linea} {etiqueta}' "
                     f"— cuota implica {prob_implicita:.0%} — {veredicto}",
                     f"Cálculo: {contexto_txt}, últimos {validos} partidos con dato")


def _analizar_goles(seleccion: str, cuota: float, extra_linea: float | None,
                    hist_home: list[dict], hist_away: list[dict], alcance: str) -> Veredicto | None:
    def _goles_partido(h):
        if h.get("goles_favor") is None:
            return None
        if alcance == "total":
            return None if h.get("goles_contra") is None else h["goles_favor"] + h["goles_contra"]
        return h["goles_favor"]
    historiales = _historiales_segun_alcance(alcance, hist_home, hist_away)
    return _analizar_stat_total(seleccion, cuota, extra_linea, historiales, _goles_partido, "goles", alcance)


def _analizar_corners(seleccion: str, cuota: float, extra_linea: float | None,
                      hist_home: list[dict], hist_away: list[dict], alcance: str) -> Veredicto | None:
    def _corners_partido(h):
        cf = h.get("a_favor", {}).get("corners")
        if cf is None:
            return None
        if alcance == "total":
            cc = h.get("en_contra", {}).get("corners")
            return None if cc is None else cf + cc
        return cf
    historiales = _historiales_segun_alcance(alcance, hist_home, hist_away)
    return _analizar_stat_total(seleccion, cuota, extra_linea, historiales, _corners_partido, "corners", alcance)


def _analizar_tarjetas(seleccion: str, cuota: float, extra_linea: float | None,
                       hist_home: list[dict], hist_away: list[dict], alcance: str) -> Veredicto | None:
    def _tarjetas_partido(h):
        af = h.get("a_favor", {})
        if af.get("amarillas") is None:
            return None
        propio = af.get("amarillas", 0) + (af.get("rojas") or 0)
        if alcance == "total":
            ec = h.get("en_contra", {})
            if ec.get("amarillas") is None:
                return None
            return propio + ec.get("amarillas", 0) + (ec.get("rojas") or 0)
        return propio
    historiales = _historiales_segun_alcance(alcance, hist_home, hist_away)
    return _analizar_stat_total(seleccion, cuota, extra_linea, historiales, _tarjetas_partido, "tarjetas", alcance)


def _analizar_btts(seleccion: str, cuota: float, hist_home: list[dict], hist_away: list[dict]) -> Veredicto | None:
    texto = seleccion.lower()
    quiere_si = texto in ("sí", "si", "yes")
    quiere_no = texto == "no"
    if not (quiere_si or quiere_no):
        return None

    def _marco(h):
        return h.get("goles_favor") is not None and h["goles_favor"] > 0

    marcaron_home = sum(1 for h in hist_home if _marco(h))
    marcaron_away = sum(1 for h in hist_away if _marco(h))
    n_home, n_away = len(hist_home), len(hist_away)
    if min(n_home, n_away) < MIN_PARTIDOS_CONFIABLE:
        return Veredicto(True, f"Muestra chica (local {n_home}, visita {n_away} partidos) — no confiar solo en esto")

    pct_home = marcaron_home / n_home if n_home else 0
    pct_away = marcaron_away / n_away if n_away else 0
    freq_btts = pct_home * pct_away   # aproximación: independencia entre ambos marcando
    prob_implicita = 1.0 / cuota
    cumple = freq_btts >= prob_implicita if quiere_si else (1 - freq_btts) >= prob_implicita

    return Veredicto(True,
                     f"Local marcó en {marcaron_home}/{n_home}, visita en {marcaron_away}/{n_away} — "
                     f"BTTS estimado ~{freq_btts:.0%} vs {prob_implicita:.0%} que implica la cuota — "
                     + ("a favor de la selección" if cumple else "en contra de la selección"))


def _normalizar_nombre(nombre: str) -> str:
    n = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", n).strip()


def _mismo_equipo(a: str, b: str) -> bool:
    """Ecuabet suele usar nombres cortos ("Hull") donde Sofascore usa
    el completo ("Hull City") — comparar substring evita que la
    selección quede sin forma/ausencias solo por eso."""
    na, nb = _normalizar_nombre(a), _normalizar_nombre(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _analizar_1x2(seleccion: str, cuota: float, home: str, away: str,
                  forma_home, forma_away, ausencias: dict | None) -> Veredicto:
    es_local = _mismo_equipo(seleccion, home)
    es_visita = not es_local and _mismo_equipo(seleccion, away)
    partes = []
    if es_local or es_visita:
        f = forma_home if es_local else forma_away
        f_riv = forma_away if es_local else forma_home
        partes.append(f"Forma propia: {f.forma_str or 'N/A'} vs forma rival: {f_riv.forma_str or 'N/A'}")
    if ausencias:
        lado_aus = ausencias.get("home" if es_local else "away", [])
        if lado_aus:
            nombres = ", ".join(a["nombre"] for a in lado_aus[:3])
            partes.append(f"⚠️ Bajas: {nombres}" + (f" y {len(lado_aus)-3} más" if len(lado_aus) > 3 else ""))
    return Veredicto(True, "1X2 — señal cualitativa, no de frecuencia (eso lo hace mejor sharp_ev.py/combo_builder.py "
                          "para picks del ciclo automático)", " | ".join(partes) if partes else "Sin datos adicionales")


def analizar_pick(pick: dict, contexto: dict) -> Veredicto:
    """pick: {mercado, seleccion, cuota, linea(opcional), equipo(opcional)}.
    `equipo`: "home" | "away" | "total"/ausente — de qué lado es la
    apuesta. Las de "Agregar manual" lo piden explícito porque el
    mercado no alcanza a decirlo (ej. "Tiros esquina: Más de 5.5" —
    ¿del partido entero, o de un equipo puntual?); los mercados que
    ya vienen de 1xbet/Ecuabet son casi siempre del partido completo,
    así que ahí "total" es la asunción por defecto.
    contexto: {home, away, forma_home, forma_away, hist_home, hist_away, ausencias}."""
    mercado = pick["mercado"].lower()
    seleccion = pick["seleccion"]
    cuota = pick["cuota"]
    linea = pick.get("linea")
    alcance = pick.get("equipo") if pick.get("equipo") in ("home", "away") else "total"

    if "corner" in mercado or "esquina" in mercado:
        v = _analizar_corners(seleccion, cuota, linea, contexto["hist_home"], contexto["hist_away"], alcance)
        if v:
            return v

    if "tarjeta" in mercado or "card" in mercado:
        v = _analizar_tarjetas(seleccion, cuota, linea, contexto["hist_home"], contexto["hist_away"], alcance)
        if v:
            return v

    if "gol" in mercado or "total" in mercado:
        v = _analizar_goles(seleccion, cuota, linea, contexto["hist_home"], contexto["hist_away"], alcance)
        if v:
            return v

    if "ambos" in mercado or "btts" in mercado:
        v = _analizar_btts(seleccion, cuota, contexto["hist_home"], contexto["hist_away"])
        if v:
            return v

    if "1x2" in mercado:
        return _analizar_1x2(seleccion, cuota, contexto["home"], contexto["away"],
                            contexto["forma_home"], contexto["forma_away"], contexto.get("ausencias"))

    return Veredicto(False, "Sin datos para cruzar este mercado todavía",
                     "No es un error — simplemente no tenemos una fuente estadística mapeada a esta selección")

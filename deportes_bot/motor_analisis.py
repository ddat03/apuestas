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

import math
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


def _analizar_marca(seleccion: str, cuota: float, hist_home: list[dict], hist_away: list[dict],
                    alcance: str) -> Veredicto | None:
    """"¿Marcará?" / "Anotará" — a diferencia de BTTS, es de UN solo
    equipo (necesita alcance="home"/"away"; con "total" no se puede
    responder, así que no se analiza)."""
    texto = seleccion.lower()
    quiere_si = texto in ("sí", "si", "yes")
    quiere_no = texto == "no"
    if not (quiere_si or quiere_no) or alcance not in ("home", "away"):
        return None

    historial = hist_home if alcance == "home" else hist_away
    marcaron = sum(1 for h in historial if h.get("goles_favor") is not None and h["goles_favor"] > 0)
    n = len(historial)
    if n < MIN_PARTIDOS_CONFIABLE:
        return Veredicto(True, f"Muestra chica ({n} partidos con datos) — no confiar solo en esto",
                         f"{marcaron}/{n} partidos recientes marcó al menos un gol")

    freq = marcaron / n
    prob_implicita = 1.0 / cuota
    cumple = freq >= prob_implicita if quiere_si else (1 - freq) >= prob_implicita
    return Veredicto(True,
                     f"Marcó en {marcaron}/{n} partidos recientes ({freq:.0%}) — cuota implica "
                     f"{prob_implicita:.0%} — " + ("a favor de la selección" if cumple else "en contra de la selección"))


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


def _novig_1x2(odds_ref: dict | None) -> dict | None:
    """Quita el margen a la cuota 1X2 de referencia -> probs que suman 1."""
    if not odds_ref or any(k not in odds_ref for k in ("local", "empate", "visitante")):
        return None
    inv = {k: 1.0 / odds_ref[k] for k in ("local", "empate", "visitante")}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s else None


def _sin_empate(probs: dict | None) -> dict | None:
    """Reparte 'local'/'visitante' quitando el empate del cálculo — para
    "Pronóstico sin empate" (Draw No Bet), donde esa opción no existe."""
    if not probs:
        return None
    total = probs.get("local", 0) + probs.get("visitante", 0)
    if total <= 0:
        return None
    return {"local": probs["local"] / total, "visitante": probs["visitante"] / total}


def _prom(historial: list[dict], extractor) -> float | None:
    """Promedio de un campo sobre los partidos que sí lo traen (no todas
    las ligas de Sofascore tienen xG/tiros cargados) — None si ninguno
    lo tiene."""
    vals = [v for v in (extractor(h) for h in historial) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _xg_favor(h: dict) -> float | None:
    v = h.get("a_favor", {}).get("goles_esperados")
    return v if v is not None else h.get("goles_favor")


def _xg_contra(h: dict) -> float | None:
    v = h.get("en_contra", {}).get("goles_esperados")
    return v if v is not None else h.get("goles_contra")


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _prob_1x2_por_stats(hist_home: list[dict], hist_away: list[dict]) -> dict | None:
    """Estimación de 1X2 con un Poisson simple: goles esperados de cada
    equipo = promedio de su propio xG a favor (o goles reales si esa liga
    no tiene xG en Sofascore) combinado con lo que el rival concede en
    promedio. Es la pata "estadística" del veredicto — independiente de
    cualquier cuota de referencia, así que sigue funcionando en las ligas
    chicas donde PrimaTips encuentra sus cuotas más bajas y Sofascore no
    tiene odds de ninguna casa. None si la muestra es muy chica."""
    if len(hist_home) < MIN_PARTIDOS_CONFIABLE or len(hist_away) < MIN_PARTIDOS_CONFIABLE:
        return None
    ataque_h, defensa_h = _prom(hist_home, _xg_favor), _prom(hist_home, _xg_contra)
    ataque_a, defensa_a = _prom(hist_away, _xg_favor), _prom(hist_away, _xg_contra)
    if None in (ataque_h, defensa_h, ataque_a, defensa_a):
        return None
    lam_h = max(0.15, (ataque_h + defensa_a) / 2)
    lam_a = max(0.15, (ataque_a + defensa_h) / 2)

    p_local = p_empate = p_visita = 0.0
    for gh in range(9):
        for ga in range(9):
            p = _poisson_pmf(gh, lam_h) * _poisson_pmf(ga, lam_a)
            if gh > ga:
                p_local += p
            elif gh == ga:
                p_empate += p
            else:
                p_visita += p
    total = p_local + p_empate + p_visita
    if total <= 0:
        return None
    return {"local": p_local / total, "empate": p_empate / total, "visitante": p_visita / total,
            "goles_esperados_local": round(lam_h, 2), "goles_esperados_visita": round(lam_a, 2)}


def _prob_estimada(p_stats: dict | None, p_mercado: dict | None, claves: tuple[str, ...]) -> tuple[float | None, str]:
    """Combina la probabilidad de stats (Poisson con tiros/goles reales) y
    la del mercado (no-vig de la cuota de referencia) para la(s) clave(s)
    pedida(s) — ('local',) para 1X2, ('local','empate') para doble
    oportunidad 1X. Promedia las dos si están ambas; si falta alguna, usa
    la que haya; dice siempre de dónde salió el número."""
    v_s = sum(p_stats[c] for c in claves) if p_stats else None
    v_m = sum(p_mercado[c] for c in claves) if p_mercado else None
    if v_s is not None and v_m is not None:
        return (v_s + v_m) / 2, "tiros/goles recientes + cuota de referencia"
    if v_s is not None:
        return v_s, "tiros/goles recientes (sin cuota de referencia para este partido)"
    if v_m is not None:
        return v_m, "cuota de referencia (muestra de tiros/goles insuficiente)"
    return None, ""


def _resumen_cifras(nombre: str, historial: list[dict]) -> str | None:
    """La línea "en cifras" que sostiene la estimación: lo que de verdad
    pidió Diego — no solo un veredicto, sino ver tiros/corners/faltas
    reales detrás. None si Sofascore no cargó nada de esto para esta
    liga (pasa en ligas chicas; ahí solo queda goles)."""
    xg = _prom(historial, _xg_favor)
    tiros = _prom(historial, lambda h: h.get("a_favor", {}).get("tiros_arco"))
    corners = _prom(historial, lambda h: h.get("a_favor", {}).get("corners"))
    faltas = _prom(historial, lambda h: h.get("a_favor", {}).get("faltas"))
    partes = []
    if xg is not None:
        partes.append(f"{xg:.1f} xG")
    if tiros is not None:
        partes.append(f"{tiros:.1f} tiros al arco")
    if corners is not None:
        partes.append(f"{corners:.1f} corners")
    if faltas is not None:
        partes.append(f"{faltas:.1f} faltas")
    if not partes:
        return None
    return f"{nombre} (últimos {len(historial)} partidos): " + ", ".join(partes) + " por partido, en promedio"


def _veredicto_valor(margen: float) -> str:
    """margen = probabilidad estimada - probabilidad que exige la cuota
    (1/cuota). Positivo = te pagan más de lo que en teoría vale."""
    if margen > 0.04:
        return "✅ CONVIENE — la cuota paga más de lo que debería"
    if margen < -0.04:
        return "❌ NO conviene — la cuota paga menos de lo que debería"
    return "➖ Al límite — la cuota ya refleja bien la probabilidad, ni gana ni pierde valor"


def _texto_calidad_rivales(nombre: str, calidad: dict | None) -> str | None:
    """El chequeo que Diego hace a mano antes de confiar en una victoria:
    ¿le ganó a alguien de más arriba en la tabla, o solo a los de abajo?
    `calidad` viene de sofascore_client.calidad_rivales_recientes —
    motor_analisis no pega a la red, solo interpreta lo que ya se trajo."""
    if not calidad or calidad.get("posicion_propia") is None:
        return None
    mejor, peor = calidad.get("victorias_vs_mejor", []), calidad.get("victorias_vs_peor", [])
    pos, total = calidad["posicion_propia"], calidad.get("total_equipos")
    ubicacion = f"{pos}º" + (f"/{total}" if total else "")
    if not mejor and not peor:
        return f"{nombre} está {ubicacion} en su tabla y no viene de ganar ningún partido reciente"
    if mejor:
        rivales = ", ".join(f"{v['rival']} ({v['posicion_rival']}º, {v['marcador']})" for v in mejor[:2])
        extra = f" y {len(mejor)-2} más" if len(mejor) > 2 else ""
        return (f"{nombre} está {ubicacion} y en sus últimos partidos le ganó a rivales MEJOR posicionados: "
                f"{rivales}{extra} — señal de confianza real, no solo pegarle a los de abajo")
    rivales = ", ".join(f"{v['rival']} ({v['posicion_rival']}º, {v['marcador']})" for v in peor[:2])
    return (f"{nombre} está {ubicacion}, pero sus victorias recientes fueron todas ante rivales PEOR "
            f"posicionados ({rivales}) — todavía no midió a un rival fuerte, más cautela")


def _linea_segura(historial: list[dict], campo: str, etiqueta: str) -> str | None:
    """Sugiere una línea "menos de X" con margen sobre el promedio
    reciente — la idea de Diego: no apostar al promedio exacto (4.2 tiros
    al arco), sino un poco arriba (menos de 5.5), y mostrar qué % de los
    últimos partidos la habría cumplido. None si no hay dato suficiente."""
    vals = [v for v in (h.get("a_favor", {}).get(campo) for h in historial) if v is not None]
    if len(vals) < MIN_PARTIDOS_CONFIABLE:
        return None
    prom = sum(vals) / len(vals)
    linea = math.ceil(prom) + 0.5
    cumplen = sum(1 for v in vals if v < linea)
    return (f"menos de {linea:g} {etiqueta} (viene de {prom:.1f} en promedio — cumplió en "
            f"{cumplen}/{len(vals)} de sus últimos partidos)")


def sugerencias_seguras(nombre: str, historial: list[dict]) -> list[str]:
    """Ideas de apuesta SIN cuota (no sale de ningún libro, solo de la
    frecuencia reciente) para mercados de tiros/corners/faltas de este
    equipo puntual — lo que Diego pidió: a partir de "4.2 tiros al arco"
    sugerir "apostar a menos de 5.5 tiros al arco", no solo mostrar el
    número. Se buscan después en Ecuabet/1xbet, esto no inventa cuota."""
    campos = (("tiros_arco", "tiros al arco"), ("corners", "corners"), ("faltas", "faltas"))
    return [f"{nombre}: {linea}" for campo, etiqueta in campos
            if (linea := _linea_segura(historial, campo, etiqueta))]


def _analizar_ganador(seleccion: str, cuota: float, home: str, away: str,
                      forma_home, forma_away, hist_home: list[dict], hist_away: list[dict],
                      ausencias: dict | None, etiqueta_mercado: str,
                      odds_referencia: dict | None = None, sin_empate: bool = False,
                      calidad_home: dict | None = None, calidad_away: dict | None = None) -> Veredicto:
    """Sirve tanto para 1X2 como para "Pronóstico sin empate" (Draw No
    Bet, sin_empate=True). Estima la probabilidad real con un Poisson de
    tiros/goles recientes (ver _prob_1x2_por_stats) y, si hay cuota de
    referencia (Sofascore/bet365), la combina con esa — y compara el
    resultado contra lo que exige tu cuota para decir derecho si conviene
    o no. Si la muestra es muy chica para ambas fuentes, es honesto: se
    queda en señal cualitativa (forma + ausencias), sin inventar un
    veredicto de valor que no puede sostener."""
    es_local = _mismo_equipo(seleccion, home)
    es_visita = not es_local and _mismo_equipo(seleccion, away)

    if not es_local and not es_visita:
        # No debería pasar casi nunca (home/away salen del mismo partido
        # que la cuota) — pero si pasa, mejor decirlo explícito que
        # mostrarle a Diego las ausencias del lado equivocado en silencio.
        return Veredicto(True, f"{etiqueta_mercado} — señal cualitativa, no de frecuencia",
                         f"No se pudo emparejar \"{seleccion}\" con \"{home}\" ni \"{away}\" — "
                         "revisar si el partido elegido es el correcto")

    lado = "local" if es_local else "visitante"
    partes = []
    for nombre, hist in ((home, hist_home), (away, hist_away)):
        r = _resumen_cifras(nombre, hist)
        if r:
            partes.append(r)
    f = forma_home if es_local else forma_away
    f_riv = forma_away if es_local else forma_home
    partes.append(f"Forma propia: {f.forma_str or 'N/A'} vs forma rival: {f_riv.forma_str or 'N/A'}")
    if ausencias:
        lado_aus = ausencias.get("home" if es_local else "away", [])
        if lado_aus:
            nombres = ", ".join(a["nombre"] for a in lado_aus[:3])
            partes.append(f"⚠️ Bajas: {nombres}" + (f" y {len(lado_aus)-3} más" if len(lado_aus) > 3 else ""))
    txt_calidad = _texto_calidad_rivales(home if es_local else away, calidad_home if es_local else calidad_away)
    if txt_calidad:
        partes.append(txt_calidad)

    p_stats = _prob_1x2_por_stats(hist_home, hist_away)
    p_mercado = _novig_1x2(odds_referencia)
    if sin_empate:
        p_stats, p_mercado = _sin_empate(p_stats), _sin_empate(p_mercado)
    p_est, fuente = _prob_estimada(p_stats, p_mercado, (lado,))

    if p_est is None:
        return Veredicto(True, f"{etiqueta_mercado} — muestra de tiros/goles insuficiente para estimar si "
                                "conviene (menos de 4 partidos recientes con datos de cada equipo) — "
                                "señal solo cualitativa, no de valor",
                         " | ".join(partes))

    p_ofrecida = 1.0 / cuota
    margen = p_est - p_ofrecida
    return Veredicto(True,
                     f"{etiqueta_mercado} — estimamos ~{p_est:.0%} de que gane {home if es_local else away} "
                     f"({fuente}) — tu cuota {cuota} necesita que pase el {p_ofrecida:.0%} de las veces para "
                     f"no perder plata — {_veredicto_valor(margen)}",
                     " | ".join(partes))


def _analizar_doble_chance(tip_code: str, cuota: float, home: str, away: str,
                           forma_home, forma_away, hist_home: list[dict], hist_away: list[dict],
                           ausencias: dict | None, odds_referencia: dict | None,
                           calidad_home: dict | None = None, calidad_away: dict | None = None) -> Veredicto:
    """tip_code: '1X' | '12' | 'X2'. Misma estimación Poisson que 1X2,
    sumando las dos claves que cubre la doble oportunidad."""
    combos = {
        "1X": (("local", "empate"), f"{home} o empate"),
        "12": (("local", "visitante"), f"{home} o {away} (no empate)"),
        "X2": (("empate", "visitante"), f"empate o {away}"),
    }
    if tip_code not in combos:
        return Veredicto(False, "Doble oportunidad — código no reconocido", tip_code)
    claves, legible = combos[tip_code]

    partes = []
    for nombre, hist in ((home, hist_home), (away, hist_away)):
        r = _resumen_cifras(nombre, hist)
        if r:
            partes.append(r)
    partes.append(f"Forma {home}: {forma_home.forma_str or 'N/A'} · Forma {away}: {forma_away.forma_str or 'N/A'}")
    if ausencias:
        for lado, etiq in (("home", home), ("away", away)):
            baj = ausencias.get(lado, [])
            if baj:
                partes.append(f"⚠️ Bajas {etiq}: " + ", ".join(x['nombre'] for x in baj[:2]))
    for nombre, calidad in ((home, calidad_home), (away, calidad_away)):
        txt_calidad = _texto_calidad_rivales(nombre, calidad)
        if txt_calidad:
            partes.append(txt_calidad)

    p_stats = _prob_1x2_por_stats(hist_home, hist_away)
    p_mercado = _novig_1x2(odds_referencia)
    p_est, fuente = _prob_estimada(p_stats, p_mercado, claves)

    if p_est is None:
        return Veredicto(True, f"Doble oportunidad {tip_code} ({legible}) — muestra de tiros/goles "
                                "insuficiente para estimar si conviene — señal solo cualitativa",
                         " | ".join(partes))

    p_ofrecida = 1.0 / cuota
    margen = p_est - p_ofrecida
    return Veredicto(True,
                     f"Doble oportunidad {tip_code} ({legible}) — estimamos ~{p_est:.0%} ({fuente}) — "
                     f"tu cuota {cuota} necesita que pase el {p_ofrecida:.0%} de las veces para no perder "
                     f"plata — {_veredicto_valor(margen)}",
                     " | ".join(partes))


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

    if "marcar" in mercado or "anota" in mercado:
        v = _analizar_marca(seleccion, cuota, contexto["hist_home"], contexto["hist_away"], alcance)
        if v:
            return v
        if alcance == "total":
            return Veredicto(False, "Sin datos para cruzar este mercado todavía",
                            "\"Marcará\" es de UN equipo puntual — al agregarla manual, elegí "
                            "local o visita en vez de \"General / total del partido\"")

    if "doble oportunidad" in mercado or "double chance" in mercado:
        codigo = (pick.get("tip") or seleccion).strip().upper()
        return _analizar_doble_chance(codigo, cuota, contexto["home"], contexto["away"],
                                      contexto["forma_home"], contexto["forma_away"],
                                      contexto["hist_home"], contexto["hist_away"],
                                      contexto.get("ausencias"), contexto.get("odds_referencia"),
                                      contexto.get("calidad_home"), contexto.get("calidad_away"))

    if "1x2" in mercado:
        return _analizar_ganador(seleccion, cuota, contexto["home"], contexto["away"],
                                contexto["forma_home"], contexto["forma_away"],
                                contexto["hist_home"], contexto["hist_away"],
                                contexto.get("ausencias"), "1X2", contexto.get("odds_referencia"),
                                calidad_home=contexto.get("calidad_home"), calidad_away=contexto.get("calidad_away"))

    if "sin empate" in mercado or "draw no bet" in mercado:
        return _analizar_ganador(seleccion, cuota, contexto["home"], contexto["away"],
                                contexto["forma_home"], contexto["forma_away"],
                                contexto["hist_home"], contexto["hist_away"],
                                contexto.get("ausencias"), "Pronóstico sin empate",
                                contexto.get("odds_referencia"), sin_empate=True,
                                calidad_home=contexto.get("calidad_home"), calidad_away=contexto.get("calidad_away"))

    return Veredicto(False, "Sin datos para cruzar este mercado todavía",
                     "No es un error — simplemente no tenemos una fuente estadística mapeada a esta selección")

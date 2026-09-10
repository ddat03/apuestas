# ============================================================
#  primatips_combos.py — Combinadas "seguras" cruzando
#  PrimaTips.com con Ecuabet
#
#  Creado por Diego Aleman.
#
#  Idea: PrimaTips publica un tip (columna T: 1 / X / 2 / doble
#  oportunidad 1X-12-X2) por partido, con la cuota de ese tip
#  (columna O). Diego arma combinadas juntando muchos favoritos
#  claros (O <= ~1.3) hasta una cuota total baja. Este módulo hace
#  ese cruce solo:
#    1. Scrapea https://primatips.com/tips/<fecha>/odd  (HTML plano,
#       server-rendered — no hay API pero tampoco hace falta JS).
#    2. Para cada tip con O <= umbral, busca ese partido en el feed
#       público de Ecuabet (el mismo que usa analizar_partido.py) y
#       saca la cuota de Ecuabet para ESA selección.
#    3. Devuelve las patas que están en Ecuabet + la cuota combinada
#       (producto de las cuotas de Ecuabet, que es donde Diego juega).
#
#  NO decide nada: PrimaTips ya eligió el tip, Ecuabet pone el
#  precio, esto solo los cruza y multiplica. La cuota de PrimaTips y
#  la de Ecuabet se muestran lado a lado para que se vea la
#  diferencia.
#
#  Límite conocido: el cruce por nombre de equipo no es perfecto
#  (PrimaTips abrevia: "Manchester Utd", "Dep. Pasto"). Se usa
#  token-overlap + un alias mínimo. Lo que no cruza se reporta
#  aparte, no se descarta en silencio.
# ============================================================

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html",
}

PRIMATIPS_URL = "https://primatips.com/tips/{fecha}/odd"

UMBRAL_ODD_DEFAULT = 1.30

# Tips que sabemos mapear a un mercado de Ecuabet. Cualquier otro
# valor de la columna T (over/under, hándicaps, etc.) se marca como
# no mapeable en vez de adivinar.
TIPS_1X2 = {"1", "X", "2"}
TIPS_DOBLE = {"1X", "12", "X2"}


# ────────────────────────────────────────────────────────────
#  Normalización / matching de nombres de equipo
# ────────────────────────────────────────────────────────────

_STOP = {
    "fc", "cf", "sc", "afc", "ac", "cd", "ca", "sd", "ec", "fk", "sk", "kf", "nk",
    "hnk", "if", "bk", "sv", "ss", "us", "as", "cs", "rc", "ur", "club", "de", "do",
    "da", "the", "sporting", "deportivo", "deportiva", "atletico", "atlético",
    "athletic", "al", "el", "la", "le", "los", "las", "ittihad", "hapoel", "maccabi",
}
_ALIAS = {
    "utd": "united", "man": "manchester", "dep": "deportivo", "ind": "independiente",
    "atl": "atletico", "intl": "international",
    "munchen": "munich", "muenchen": "munich", "praga": "prague",
}

# Marcadores que hacen que dos equipos NO sean el mismo aunque el
# nombre base coincida: filial ("B", "II"), sub-XX, femenino.
_MARCADOR_RE = re.compile(
    r"(?<![a-z])(b|ii|iii|u\s?\d{2}|sub\s?\d{2}|reserves?|res|amateur|am|"
    r"w|women|femenino|feminino|feminine|fem|ladies|\(f\))(?![a-z])"
)


def _marcador(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().lower()
    m = _MARCADOR_RE.search(s)
    return re.sub(r"\s", "", m.group(1)) if m else ""


def _tokens(nombre: str) -> set[str]:
    s = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = []
    for w in s.split():
        w = _ALIAS.get(w, w)
        if w and w not in _STOP and len(w) > 1:
            toks.append(w)
    return set(toks)


def _mismo_equipo(a: str, b: str) -> bool:
    if _marcador(a) != _marcador(b):
        return False   # filial / femenino vs equipo principal
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    chico, grande = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if chico <= grande:
        # subconjunto: aceptable si el chico ya tiene 2+ tokens, o si al
        # grande solo le sobra 1 (típico prefijo/sufijo: "AE", "FC", ciudad)
        return len(chico) >= 2 or (len(grande) - len(chico)) <= 1
    return len(ta & tb) >= 2


def _mismo_partido(ph: str, pa: str, eh: str, ea: str) -> bool:
    """Home con home y away con away — NO cruza fixtures invertidos
    (ida/vuelta de una llave son partidos distintos)."""
    return _mismo_equipo(ph, eh) and _mismo_equipo(pa, ea)


# ────────────────────────────────────────────────────────────
#  Scraping de PrimaTips
# ────────────────────────────────────────────────────────────

@dataclass
class TipPrimatips:
    home: str
    away: str
    liga: str
    hora: str          # "HH:MM" en la timezone del server de primatips (UTC)
    tip: str            # "1" | "X" | "2" | "1X" | "12" | "X2" | otro
    odd: float | None   # cuota de ese tip según primatips
    href: str = ""


def scrapear_primatips(fecha: str | date | None = None) -> list[TipPrimatips]:
    """fecha: 'YYYY-MM-DD' o date. None = hoy (UTC, que es la tz del
    server de primatips)."""
    if fecha is None:
        fecha = datetime.now(timezone.utc).date()
    if isinstance(fecha, date):
        fecha = fecha.isoformat()

    r = requests.get(PRIMATIPS_URL.format(fecha=fecha), headers=HEADERS, timeout=25)
    r.raise_for_status()
    t = r.text

    tips: list[TipPrimatips] = []
    for bloque in re.split(r'(?=<a id="g_\d+" href="/tip/)', t):
        if not bloque.startswith('<a id="g_'):
            continue
        # el HTML de primatips a veces parte el cierre de tag ("</span\n>"),
        # así que se corta en "</span" y no en "</span>".
        nms = re.findall(r'<span class="nm">([^<]+)</span', bloque)
        m_tip = re.search(r'<span class="tip">([^<]*)</span', bloque)
        m_odd = re.search(r'<span class="odd">([^<]*)</span', bloque)
        if len(nms) < 2 or not m_tip:
            continue

        m_hora = re.search(r'<span class="tm">([^<]*)</span', bloque)
        m_liga = re.search(r'<img [^>]*title="([^"]*)"', bloque)
        m_href = re.search(r'href="(/tip/[^"]+)"', bloque)

        odd_txt = (m_odd.group(1).strip() if m_odd else "")
        try:
            odd = float(odd_txt) if odd_txt else None
        except ValueError:
            odd = None

        tips.append(TipPrimatips(
            home=html.unescape(nms[0]).strip(),
            away=html.unescape(nms[1]).strip(),
            liga=html.unescape(m_liga.group(1)).strip() if m_liga else "",
            hora=m_hora.group(1).strip() if m_hora else "",
            tip=m_tip.group(1).strip(),
            odd=odd,
            href=m_href.group(1) if m_href else "",
        ))
    return tips


# ────────────────────────────────────────────────────────────
#  Cruce con Ecuabet
# ────────────────────────────────────────────────────────────

@dataclass
class PataCruzada:
    tip: TipPrimatips
    en_ecuabet: bool
    motivo: str = ""                 # por qué no está (si en_ecuabet=False)
    ecuabet_home: str = ""
    ecuabet_away: str = ""
    mercado_ecuabet: str = ""        # "1x2" | "Doble oportunidad"
    seleccion_ecuabet: str = ""      # texto legible de la opción elegida
    cuota_ecuabet: float | None = None

    @property
    def tip_legible(self) -> str:
        mapa = {
            "1": f"{self.tip.home} gana",
            "X": "Empate",
            "2": f"{self.tip.away} gana",
            "1X": f"{self.tip.home} o empate",
            "12": f"{self.tip.home} o {self.tip.away} (no empate)",
            "X2": f"empate o {self.tip.away}",
        }
        return mapa.get(self.tip.tip, self.tip.tip)


def _dt_ecuabet(ev: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(ev["startDate"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return None


def _hora_cerca(primatips_hora: str, ev_dt: datetime | None, tol_horas: float = 3.5) -> bool:
    """PrimaTips da la hora en UTC (su server_timezone es Etc/GMT+0);
    Ecuabet también da startDate en UTC. Si alguno falta, no bloquea."""
    if not primatips_hora or ev_dt is None:
        return True
    try:
        hh, mm = (int(x) for x in primatips_hora.split(":"))
    except ValueError:
        return True
    p_min = hh * 60 + mm
    e_min = ev_dt.hour * 60 + ev_dt.minute
    diff = min(abs(p_min - e_min), 1440 - abs(p_min - e_min))  # wrap medianoche
    return diff <= tol_horas * 60


def _mercado_por_nombre(mercados: list[dict], *needles: str) -> dict | None:
    for m in mercados:
        n = (m.get("mercado") or "").lower()
        if any(x in n for x in needles):
            return m
    return None


def _cuota_1x2(opciones: dict, tip: str, ec_home: str, ec_away: str) -> tuple[str, float] | None:
    # opciones: {nombre_equipo|"Empate": cuota}, en orden [home, empate, away]
    keys = list(opciones)
    empate_key = next((k for k in keys if "empate" in k.lower() or "draw" in k.lower()), None)
    home_key = next((k for k in keys if k != empate_key and _mismo_equipo(k, ec_home)), None)
    away_key = next((k for k in keys if k not in (empate_key, home_key) and _mismo_equipo(k, ec_away)), None)

    # fallback por posición si no se pudo mapear por nombre (Ecuabet lista
    # siempre local / empate / visitante en ese orden)
    if len(keys) == 3:
        home_key = home_key or (keys[0] if keys[0] != empate_key else None)
        away_key = away_key or (keys[2] if keys[2] != empate_key else None)
        empate_key = empate_key or keys[1]

    objetivo = {"1": home_key, "X": empate_key, "2": away_key}.get(tip)
    if objetivo and opciones.get(objetivo) is not None:
        return objetivo, float(opciones[objetivo])
    return None


def _menciona(texto: str, equipo: str) -> bool:
    """¿El texto de la opción nombra a este equipo? (las opciones de
    doble oportunidad son compuestas: "Enköpings SK o Assyriska FF")."""
    tt, te = _tokens(texto), _tokens(equipo)
    return bool(te) and bool(te & tt)


def _cuota_doble(opciones: dict, tip: str, ec_home: str, ec_away: str) -> tuple[str, float] | None:
    for k, v in opciones.items():
        if v is None:
            continue
        hay_empate = "empate" in k.lower() or "draw" in k.lower()
        hay_home = _menciona(k, ec_home)
        hay_away = _menciona(k, ec_away)
        clave = None
        if hay_home and hay_empate:
            clave = "1X"
        elif hay_away and hay_empate:
            clave = "X2"
        elif hay_home and hay_away and not hay_empate:
            clave = "12"
        if clave == tip:
            return k, float(v)
    return None


def cruzar_con_ecuabet(tips: list[TipPrimatips], fecha: str | date,
                       ecuabet_ctx: dict, get_mercados_ecuabet) -> list[PataCruzada]:
    """`ecuabet_ctx` y `get_mercados_ecuabet` vienen de analizar_partido
    (se pasan como argumentos para no crear un import circular con el
    dashboard). `get_mercados_ecuabet(ev, ctx) -> [{"mercado","opciones"}]`."""
    if isinstance(fecha, str):
        fecha = date.fromisoformat(fecha)
    comp = ecuabet_ctx["competidores"]
    eventos = ecuabet_ctx["events"]

    # candidatos de Ecuabet en la misma fecha (± 1 día por timezone)
    candidatos = []
    for ev in eventos:
        ci = ev.get("competitorIds", [])
        if len(ci) != 2:
            continue
        dt = _dt_ecuabet(ev)
        if dt is not None and abs((dt.date() - fecha).days) > 1:
            continue
        candidatos.append((comp.get(ci[0], "").strip(), comp.get(ci[1], "").strip(), ev, dt))

    salida: list[PataCruzada] = []
    for tp in tips:
        if tp.tip not in TIPS_1X2 and tp.tip not in TIPS_DOBLE:
            salida.append(PataCruzada(tp, False, motivo=f"tip '{tp.tip}' no es 1X2 ni doble oportunidad"))
            continue

        ev_match = next(
            (c for c in candidatos
             if _mismo_partido(tp.home, tp.away, c[0], c[1]) and _hora_cerca(tp.hora, c[3])),
            None,
        )
        if not ev_match:
            salida.append(PataCruzada(tp, False, motivo="partido no está en Ecuabet"))
            continue

        eh, ea, ev, _ = ev_match
        mercados = get_mercados_ecuabet(ev, ecuabet_ctx)

        if tp.tip in TIPS_1X2:
            m = _mercado_por_nombre(mercados, "1x2", "1 x 2")
            res = _cuota_1x2(m["opciones"], tp.tip, eh, ea) if m else None
            nombre_mercado = "1x2"
        else:
            m = _mercado_por_nombre(mercados, "doble oportunidad", "double chance")
            res = _cuota_doble(m["opciones"], tp.tip, eh, ea) if m else None
            nombre_mercado = "Doble oportunidad"

        if not res:
            salida.append(PataCruzada(
                tp, False, motivo=f"Ecuabet no expone el mercado '{nombre_mercado}' para este partido",
                ecuabet_home=eh, ecuabet_away=ea))
            continue

        seleccion, cuota = res
        salida.append(PataCruzada(
            tp, True, ecuabet_home=eh, ecuabet_away=ea,
            mercado_ecuabet=nombre_mercado, seleccion_ecuabet=seleccion, cuota_ecuabet=cuota))
    return salida


# ────────────────────────────────────────────────────────────
#  Armado de la combinada
# ────────────────────────────────────────────────────────────

@dataclass
class Combinada:
    patas: list[PataCruzada] = field(default_factory=list)   # solo en_ecuabet y O <= umbral
    fuera_umbral: list[PataCruzada] = field(default_factory=list)
    no_cruzadas: list[PataCruzada] = field(default_factory=list)
    umbral: float = UMBRAL_ODD_DEFAULT

    @property
    def no_cruzadas_en_umbral(self) -> list[PataCruzada]:
        """Las que NO están en Ecuabet pero SÍ tienen O <= umbral —
        las que 'faltan' para la combinada, no todo el ruido."""
        return [p for p in self.no_cruzadas if p.tip.odd is not None and p.tip.odd <= self.umbral]

    @property
    def cuota_total_ecuabet(self) -> float:
        total = 1.0
        for p in self.patas:
            total *= (p.cuota_ecuabet or 1.0)
        return round(total, 3)

    @property
    def cuota_total_primatips(self) -> float:
        total = 1.0
        for p in self.patas:
            total *= (p.tip.odd or 1.0)
        return round(total, 3)


def armar_combinada(cruzadas: list[PataCruzada], umbral: float = UMBRAL_ODD_DEFAULT) -> Combinada:
    c = Combinada(umbral=umbral)
    for p in cruzadas:
        if not p.en_ecuabet:
            c.no_cruzadas.append(p)
        elif p.tip.odd is not None and p.tip.odd <= umbral:
            c.patas.append(p)
        else:
            c.fuera_umbral.append(p)
    c.patas.sort(key=lambda p: p.tip.odd or 99)
    return c


# ────────────────────────────────────────────────────────────
#  Versión de consola (rápida, para probar sin el dashboard)
# ────────────────────────────────────────────────────────────

def main() -> None:
    import sys
    import analizar_partido as ap

    fecha = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone.utc).date().isoformat()
    print(f"PrimaTips {fecha} ...")
    tips = scrapear_primatips(fecha)
    print(f"  {len(tips)} tips en total")

    print("Cargando feed de Ecuabet (~30s)...")
    ctx = ap._cargar_ecuabet_raw()
    cruzadas = cruzar_con_ecuabet(tips, fecha, ctx, ap.get_mercados_ecuabet)
    combo = armar_combinada(cruzadas)

    print(f"\n=== COMBINADA (O <= {combo.umbral}, en Ecuabet) — {len(combo.patas)} patas ===")
    for p in combo.patas:
        print(f"  {p.tip.home} vs {p.tip.away}  [{p.tip.liga}]")
        print(f"     {p.tip_legible}  |  PrimaTips O={p.tip.odd}   Ecuabet={p.cuota_ecuabet} ({p.seleccion_ecuabet})")
    print(f"\n  CUOTA TOTAL Ecuabet:  {combo.cuota_total_ecuabet}")
    print(f"  (referencia PrimaTips: {combo.cuota_total_primatips})")

    if combo.no_cruzadas:
        print(f"\n  No cruzadas / sin mercado en Ecuabet ({len(combo.no_cruzadas)}):")
        for p in combo.no_cruzadas:
            if p.tip.odd and p.tip.odd <= combo.umbral:
                print(f"     {p.tip.home} vs {p.tip.away}  (O={p.tip.odd})  — {p.motivo}")


if __name__ == "__main__":
    main()

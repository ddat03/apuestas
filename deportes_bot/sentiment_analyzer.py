# ============================================================
#  sentiment_analyzer.py — Sentimiento de noticias (pre-partido)
#
#  Extrae noticias de última hora via Google News RSS
#  y las analiza con OpenAI para detectar:
#    - Lesiones de último minuto no reportadas por la API
#    - Polémicas / conflictos internos del equipo
#    - Cambios de alineación filtrados a medios
#    - Condiciones del estadio / viaje / clima extremo
#
#  No requiere Twitter API (es de pago). Usa Google News RSS
#  que es completamente gratuito.
# ============================================================

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import quote

import requests

log = logging.getLogger("SentimentAnalyzer")

GOOGLE_NEWS_URL = "https://news.google.com/rss/search"
TIMEOUT = 8

# Palabras clave que indican noticias negativas de alto impacto
KEYWORDS_NEGATIVOS = [
    "lesión", "lesionado", "baja", "no jugará", "out", "injured",
    "suspension", "suspendido", "expelled", "tarjeta roja",
    "conflicto", "pelea", "protesta", "huelga", "sanción",
    "vuelo cancelado", "covid", "enfermedad",
]

KEYWORDS_POSITIVOS = [
    "regresa", "recuperado", "vuelve", "available", "fit",
    "goleador en forma", "racha ganadora",
]


@dataclass
class SentimientoPartido:
    noticias_local:   list[str] = field(default_factory=list)
    noticias_visita:  list[str] = field(default_factory=list)
    alertas:          list[str] = field(default_factory=list)   # noticias de alto impacto
    score_local:      float     = 0.0   # -1.0 muy negativo → +1.0 muy positivo
    score_visita:     float     = 0.0
    resumen_ia:       str       = ""    # análisis en español generado por OpenAI
    disponible:       bool      = False


def _buscar_noticias(termino: str, idioma: str = "es", n: int = 5) -> list[str]:
    """Descarga RSS de Google News y extrae los titulares."""
    try:
        url = f"{GOOGLE_NEWS_URL}?q={quote(termino)}&hl={idioma}&gl=ES&ceid=ES:es"
        r   = requests.get(url, timeout=TIMEOUT,
                           headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()

        root  = ET.fromstring(r.text)
        items = root.findall(".//item")
        noticias = []
        for item in items[:n]:
            titulo  = item.findtext("title", "")
            desc    = item.findtext("description", "")
            # limpiar HTML básico
            desc_limpio = re.sub(r"<[^>]+>", "", desc)
            noticias.append(f"{titulo}. {desc_limpio[:200]}")

        return noticias
    except Exception as e:
        log.warning(f"Google News RSS para '{termino}': {e}")
        return []


def _score_simple(noticias: list[str]) -> float:
    """Score -1 a +1 basado en keywords, sin IA (fallback rápido)."""
    score = 0.0
    for noticia in noticias:
        n = noticia.lower()
        for kw in KEYWORDS_NEGATIVOS:
            if kw in n:
                score -= 0.3
        for kw in KEYWORDS_POSITIVOS:
            if kw in n:
                score += 0.2
    return max(-1.0, min(1.0, score))


def analizar_sentimiento(equipo_local: str, equipo_visita: str,
                          liga: str = "",
                          usar_ia: bool = True) -> SentimientoPartido:
    """
    Busca noticias de los dos equipos y genera un análisis de sentimiento.
    Si usar_ia=True y OPENAI_API_KEY está configurado, usa OpenAI para analizar.
    """
    resultado = SentimientoPartido()

    # ── 1. Buscar noticias ────────────────────────────────
    termino_l = f"{equipo_local} partido {liga}"
    termino_v = f"{equipo_visita} partido {liga}"

    resultado.noticias_local  = _buscar_noticias(termino_l)
    resultado.noticias_visita = _buscar_noticias(termino_v)

    if not resultado.noticias_local and not resultado.noticias_visita:
        resultado.resumen_ia = "Sin noticias disponibles"
        return resultado

    resultado.disponible  = True
    resultado.score_local  = _score_simple(resultado.noticias_local)
    resultado.score_visita = _score_simple(resultado.noticias_visita)

    # ── 2. Detectar alertas críticas ─────────────────────
    todas = resultado.noticias_local + resultado.noticias_visita
    for noticia in todas:
        n = noticia.lower()
        if any(kw in n for kw in KEYWORDS_NEGATIVOS[:8]):   # lesiones, suspensiones
            resultado.alertas.append(noticia[:150])

    # ── 3. Análisis con OpenAI (opcional) ─────────────────
    if usar_ia:
        try:
            import os
            from openai import OpenAI
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise ValueError("Sin OPENAI_API_KEY")

            client = OpenAI(api_key=api_key)

            noticias_texto = "\n".join([
                f"[{equipo_local}]: " + n for n in resultado.noticias_local[:3]
            ] + [
                f"[{equipo_visita}]: " + n for n in resultado.noticias_visita[:3]
            ])

            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
                max_tokens=300,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": (
                        "Eres analista deportivo. Analiza estas noticias pre-partido en 3-4 líneas. "
                        "Destaca solo lo que afecte materialmente el resultado: lesiones de titulares, "
                        "suspensiones, conflictos, rotaciones confirmadas. Ignora noticias sin impacto. "
                        "Si no hay nada relevante, di 'Sin alertas relevantes'. Responde en español."
                    )},
                    {"role": "user", "content": (
                        f"Partido: {equipo_local} vs {equipo_visita}\n\n"
                        f"Noticias recientes:\n{noticias_texto}"
                    )},
                ],
            )
            resultado.resumen_ia = resp.choices[0].message.content.strip()

        except Exception as e:
            log.warning(f"OpenAI sentimiento falló: {e}")
            resultado.resumen_ia = _resumen_simple(resultado)
    else:
        resultado.resumen_ia = _resumen_simple(resultado)

    log.info(f"Sentimiento {equipo_local}: {resultado.score_local:+.2f} | "
             f"{equipo_visita}: {resultado.score_visita:+.2f} | "
             f"alertas={len(resultado.alertas)}")
    return resultado


def _resumen_simple(s: SentimientoPartido) -> str:
    if not s.disponible:
        return "Sin noticias"
    partes = []
    if s.alertas:
        partes.append(f"⚠️ {len(s.alertas)} alerta(s) de impacto detectada(s)")
    if s.score_local < -0.3:
        partes.append("Noticias negativas del equipo local")
    if s.score_visita < -0.3:
        partes.append("Noticias negativas del equipo visitante")
    return " | ".join(partes) if partes else "Sin alertas relevantes"


def resumen_para_prompt(s: SentimientoPartido) -> str:
    if not s.disponible:
        return "  Sentimiento: Sin datos de noticias"
    lineas = ["SENTIMIENTO / NOTICIAS PRE-PARTIDO:"]
    if s.resumen_ia:
        for linea in s.resumen_ia.split("\n"):
            lineas.append(f"  {linea}")
    if s.alertas:
        lineas.append(f"  ⚠️ ALERTAS ({len(s.alertas)}):")
        for a in s.alertas[:3]:
            lineas.append(f"    - {a}")
    return "\n".join(lineas)

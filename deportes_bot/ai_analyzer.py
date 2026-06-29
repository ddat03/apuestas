# ============================================================
#  ai_analyzer.py — Análisis con OpenAI GPT
#
#  Filosofía: PROBABILIDAD ALTA (≥60%), no cuota alta.
#  Una cuota de 1.15 con 90% de probabilidad es mejor
#  que una cuota de 5.0 con 20% de probabilidad.
#
#  Combina datos reales de API + conocimiento propio de la IA
#  para estimar mercados sin datos (corners, tarjetas, etc.)
# ============================================================

import json
import logging
import os
from datetime import datetime

log = logging.getLogger("AIAnalyzer")

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# ══════════════════════════════════════════
#  PROMPTS
# ══════════════════════════════════════════

SYSTEM_PROMPT = """Eres un analista experto en apuestas deportivas. Tu trabajo es identificar
selecciones con ALTA PROBABILIDAD (60% o más), no cuotas altas.

FILOSOFÍA:
- Una cuota de 1.15 con 88% de probabilidad ES una buena apuesta.
- Una cuota de 5.0 con 20% de probabilidad NO te interesa.
- Al combinar varias selecciones seguras (1.15 × 1.20 × 1.25 = 1.72), el resultado ya es bueno.
- Prioriza siempre la certeza sobre el beneficio potencial.

UMBRAL MÍNIMO: NO incluyas ninguna selección con probabilidad estimada menor a 60%.
Si no hay suficientes selecciones confiables, di que no hay apuestas hoy.

MERCADOS QUE PUEDES ESTIMAR (aunque no tengas datos de API):
- Goles: Más/Menos 0.5, 1.5, 2.5, 3.5 (usa el nivel ofensivo y defensivo de cada equipo)
- Corners: Más/Menos 8.5, 9.5, 10.5 (equipos con juego amplio generan más corners)
- Tarjetas: Más/Menos 2.5, 3.5 (partidos muy competidos generan más faltas)
- Ambos marcan (BTTS): Sí/No
- Resultado al descanso (1ª parte)
- Handicap asiático (si el favorito es muy claro)
- El favorito anota primero

IMPORTANTE: Responde SIEMPRE con JSON válido exactamente con la estructura pedida."""


def _build_partido_context(partido: dict, mercados: dict,
                           noticia: str = "") -> str:
    """Construye el bloque de texto de un partido para el prompt."""
    home = partido.get("equipo_local", "")
    away = partido.get("equipo_visitante", "")

    lines = [
        f"PARTIDO: {home} vs {away}",
        f"Competición: {partido.get('liga_nombre','')} | Fecha: {partido.get('fecha','')} {partido.get('hora','')}",
    ]

    if noticia:
        lines += ["", "NOTICIAS PRE-PARTIDO (considera esto al estimar probabilidades):", noticia]

    lines += ["", "PROBABILIDADES CONSENSO DEL MERCADO (sin margen de casa):"]

    # H2H — mostrar solo la probabilidad, que es lo que importa
    h2h = mercados.get("h2h", {})
    if h2h:
        for k, m in h2h.items():
            p = m.get("prob_cons", 0)
            c = m.get("cuota_best", 0)
            marca = " ← SUPERA 60%" if p >= 0.60 else ""
            lines.append(f"  {m['etiqueta']}: prob={p:.0%}  cuota={c}{marca}")

    # Totals (Over/Under goles) — de API si existen
    totals = mercados.get("totals", {})
    if totals:
        lines.append("")
        lines.append("MERCADO GOLES (datos de bookmakers):")
        for k, m in sorted(totals.items()):
            if m.get("n_books", 0) >= 3:
                p = m.get("prob_cons", 0)
                c = m.get("cuota_best", 0)
                marca = " ← SUPERA 60%" if p >= 0.60 else ""
                lines.append(f"  {m['etiqueta']}: prob={p:.0%}  cuota={c}{marca}")

    lines += [
        "",
        "MERCADOS SIN DATOS DE API (estima con tu conocimiento):",
        f"  → Corners, Tarjetas, BTTS, 1ª parte para {home} vs {away}",
        f"  → Usa tu conocimiento del estilo de juego, historial y contexto del torneo.",
    ]

    return "\n".join(lines)


USER_PROMPT_TEMPLATE = """Analiza los siguientes partidos. SOLO incluye selecciones con probabilidad ≥ 60%.

HOY: {fecha}
BANKROLL: ${bankroll} | MÁXIMO POR APUESTA: ${max_apuesta}

REGLAS ESTRICTAS:
1. Probabilidad mínima: 60%. Si estimas que algo tiene menos del 60%, NO lo incluyas.
2. Cuota baja no es problema: 1.10, 1.15, 1.20 con alta probabilidad son bienvenidas.
3. Para combinadas: usa SOLO patas con probabilidad ≥ 65%. Calcula la probabilidad
   combinada real (multiplica las probabilidades individuales).
4. Incluye mercados estimados (corners, tarjetas) si tu confianza es genuinamente alta.
5. Sé honesto: si un partido no tiene selecciones ≥ 60%, no lo incluyas.

{partidos_contexto}

Responde EXACTAMENTE con este JSON (sin texto fuera):

{{
  "apuestas_individuales": [
    {{
      "partido": "Germany vs Paraguay",
      "descripcion": "Alemania gana",
      "mercado": "h2h_local",
      "cuota": 1.38,
      "prob_estimada": 0.76,
      "confianza": "alta",
      "apuesta_sugerida": {max_apuesta},
      "razon": "Razón concreta en una frase"
    }}
  ],
  "combinadas": [
    {{
      "tipo": "Segura",
      "patas": [
        {{
          "partido": "Germany vs Paraguay",
          "seleccion": "Alemania gana",
          "mercado": "h2h_local",
          "cuota": 1.38,
          "prob": 0.76
        }},
        {{
          "partido": "France vs Sweden",
          "seleccion": "Francia gana",
          "mercado": "h2h_local",
          "cuota": 1.31,
          "prob": 0.82
        }}
      ],
      "cuota_total": 1.81,
      "prob_total": 0.62,
      "apuesta_sugerida": {max_apuesta},
      "razon": "Ambos favoritos muy claros con alta probabilidad"
    }},
    {{
      "tipo": "Equilibrada",
      "patas": [
        {{
          "partido": "Germany vs Paraguay",
          "seleccion": "Más de 1.5 goles",
          "mercado": "over_1_5",
          "cuota": 1.18,
          "prob": 0.85
        }},
        {{
          "partido": "France vs Sweden",
          "seleccion": "Francia gana o empata (no pierde)",
          "mercado": "doble_chance_local",
          "cuota": 1.08,
          "prob": 0.92
        }},
        {{
          "partido": "Brazil vs Japan",
          "seleccion": "Brasil gana",
          "mercado": "h2h_local",
          "cuota": 1.77,
          "prob": 0.67
        }}
      ],
      "cuota_total": 2.26,
      "prob_total": 0.53,
      "apuesta_sugerida": {max_apuesta},
      "razon": "Tres selecciones muy probables con cuota combinada atractiva"
    }},
    {{
      "tipo": "Mixta",
      "patas": [],
      "cuota_total": 0,
      "prob_total": 0,
      "apuesta_sugerida": {max_apuesta},
      "razon": "Mezcla de resultado + mercado de goles/corners con alta probabilidad"
    }},
    {{
      "tipo": "Larga",
      "patas": [],
      "cuota_total": 0,
      "prob_total": 0,
      "apuesta_sugerida": {max_apuesta},
      "razon": "4-5 patas, todas con prob ≥ 65%, cuota acumulada más alta"
    }}
  ]
}}

NOTA: Si un tipo de combinada no tiene suficientes patas confiables, déjala con patas vacías.
No inventes probabilidades. Si no estás seguro, no lo incluyas."""


# ══════════════════════════════════════════
#  CLIENTE OPENAI
# ══════════════════════════════════════════

def analizar_con_ia(
    partidos: list[dict],
    mercados: list[dict],
    bankroll: float = 100.0,
    max_apuesta: float = 5.0,
    noticias_por_partido: dict | None = None,
) -> dict:
    if not _OPENAI_AVAILABLE:
        return {"error": "openai no instalado. pip install openai"}
    if not OPENAI_API_KEY:
        return {"error": "OPENAI_API_KEY no configurada en .env"}

    noticias_por_partido = noticias_por_partido or {}

    partidos_ctx = []
    for partido, mercado in zip(partidos, mercados):
        key    = f"{partido.get('equipo_local','')} vs {partido.get('equipo_visitante','')}"
        noticia = noticias_por_partido.get(key, "")
        ctx    = _build_partido_context(partido, mercado, noticia=noticia)
        partidos_ctx.append(f"{'─'*50}\n{ctx}")

    user_prompt = USER_PROMPT_TEMPLATE.format(
        fecha=datetime.now().strftime("%Y-%m-%d"),
        bankroll=bankroll,
        max_apuesta=max_apuesta,
        partidos_contexto="\n\n".join(partidos_ctx),
    )

    log.info(f"Enviando {len(partidos)} partidos a OpenAI ({OPENAI_MODEL})…")

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=4000,
        )
        raw      = response.choices[0].message.content
        resultado= json.loads(raw)
        tokens   = response.usage.total_tokens
        costo    = tokens / 1_000_000 * 0.60
        log.info(f"✓ OpenAI | tokens={tokens} | costo≈${costo:.4f}")

        resultado = _filtrar_baja_probabilidad(resultado)
        # Guardar noticias resumen para el mensaje Telegram
        resultado["_noticias_resumen"] = _resumir_noticias(noticias_por_partido)
        return resultado

    except json.JSONDecodeError as e:
        log.error(f"JSON inválido de OpenAI: {e}")
        return {"error": f"JSON inválido: {e}"}
    except Exception as e:
        log.error(f"Error OpenAI: {e}")
        return {"error": str(e)}


def _filtrar_baja_probabilidad(analisis: dict) -> dict:
    """Elimina cualquier selección que OpenAI haya incluido con prob < 60%."""
    # Filtrar individuales
    individuales = analisis.get("apuestas_individuales", [])
    analisis["apuestas_individuales"] = [
        a for a in individuales
        if float(a.get("prob_estimada", 0)) >= 0.60
    ]

    # Filtrar combinadas: eliminar patas con prob < 65% y recalcular
    combinadas_limpias = []
    for comb in analisis.get("combinadas", []):
        patas = [p for p in comb.get("patas", []) if float(p.get("prob", 0)) >= 0.65]
        if len(patas) < 2:
            continue  # combinada sin suficientes patas → descartar
        # Recalcular cuota y prob total
        cuota_total = round(_producto([float(p.get("cuota", 1)) for p in patas]), 3)
        prob_total  = round(_producto([float(p.get("prob",  1)) for p in patas]), 3)
        comb["patas"]       = patas
        comb["cuota_total"] = cuota_total
        comb["prob_total"]  = prob_total
        combinadas_limpias.append(comb)

    analisis["combinadas"] = combinadas_limpias
    return analisis


def _producto(valores: list[float]) -> float:
    r = 1.0
    for v in valores:
        r *= v
    return r


def _resumir_noticias(noticias_por_partido: dict) -> str:
    """Compacta todas las noticias en un bloque para el mensaje Telegram."""
    if not noticias_por_partido:
        return ""
    lineas = []
    for partido, texto in noticias_por_partido.items():
        if texto.strip():
            lineas.append(f"*{partido}*\n{texto.strip()}")
    return "\n\n".join(lineas)


# ══════════════════════════════════════════
#  FORMATEO PARA TELEGRAM
# ══════════════════════════════════════════

def formatear_analisis_ia(analisis: dict) -> str:
    if "error" in analisis:
        return f"⚠️ Sin análisis IA: {analisis['error']}"

    tipo_emoji = {"Segura": "🟢", "Equilibrada": "🔵", "Mixta": "🟡", "Larga": "🟠"}
    conf_emoji = {"alta": "🔥", "media": "✅", "baja": "⚠️"}

    lines = [
        f"⚽ *APUESTAS DEL DÍA — {datetime.now().strftime('%d/%m/%Y')}*",
        "━" * 30,
    ]

    # ── Apuestas individuales ─────────────────────────────
    individuales = analisis.get("apuestas_individuales", [])
    if individuales:
        lines += ["", "📌 *APUESTAS FIJAS*", ""]
        for ap in individuales:
            prob = float(ap.get("prob_estimada", 0))
            ce   = conf_emoji.get(ap.get("confianza", ""), "⚪")
            apo  = float(ap.get("apuesta_sugerida", 0))
            lines += [
                f"{ce} *{ap.get('partido', '')}*",
                f"   ✔ {ap.get('descripcion', '')}  @  cuota *{ap.get('cuota', '')}*",
                f"   Probabilidad: *{prob:.0%}*  |  Apostar: *${apo:.2f}*",
                f"   _{ap.get('razon', '')}_",
                "",
            ]
    else:
        lines += ["", "ℹ️ Sin apuestas fijas con probabilidad ≥ 60% hoy.", ""]

    # ── Combinadas ────────────────────────────────────────
    combinadas = analisis.get("combinadas", [])
    if combinadas:
        lines += ["─" * 30, "", "🎯 *COMBINADAS*", ""]
        orden = {"Segura": 1, "Equilibrada": 2, "Mixta": 3, "Larga": 4}
        for comb in sorted(combinadas, key=lambda x: orden.get(x.get("tipo", ""), 9)):
            tipo      = comb.get("tipo", "")
            cuota_t   = comb.get("cuota_total", 0)
            prob_t    = float(comb.get("prob_total", 0))
            apuesta   = float(comb.get("apuesta_sugerida", 0))
            patas     = comb.get("patas", [])
            emoji     = tipo_emoji.get(tipo, "⚪")
            ganancia  = round(apuesta * (float(cuota_t) - 1), 2) if cuota_t else 0

            lines.append(f"{emoji} *{tipo}*  —  cuota {cuota_t}  |  prob {prob_t:.0%}  |  ${apuesta:.2f}")
            for p in patas:
                lines.append(f"   + {p.get('partido','')}  →  {p.get('seleccion','')}  ({p.get('cuota','')} / {float(p.get('prob',0)):.0%})")
            lines.append(f"   _Ganarías ${ganancia:.2f} si aciertas las {len(patas)} patas_")
            lines.append(f"   _{comb.get('razon', '')}_")
            lines.append("")

    # ── Sección de noticias ───────────────────────────────
    noticias = analisis.get("_noticias_resumen", "")
    if noticias:
        lines += ["", "─" * 30, "", "📰 *NOTICIAS PRE-PARTIDO*", ""]
        for linea in noticias.split("\n"):
            lines.append(linea)
        lines.append("")

    lines.append("_Solo análisis. Tú decides si apostar._")
    return "\n".join(lines)

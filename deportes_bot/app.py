#!/usr/bin/env python3
# ============================================================
#  app.py — Dashboard web con Streamlit
#
#  Iniciar:
#    cd deportes_bot
#    streamlit run app.py
# ============================================================

import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from dotenv import load_dotenv
load_dotenv()

import streamlit as st

# ─── Config de página ────────────────────────────────────
st.set_page_config(
    page_title="⚽ Sistema Apuestas",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Estilos ─────────────────────────────────────────────
st.markdown("""
<style>
.big-metric { font-size: 2rem; font-weight: bold; }
.prob-high  { color: #00c851; font-weight: bold; }
.prob-med   { color: #ffbb33; font-weight: bold; }
.prob-low   { color: #ff4444; font-weight: bold; }
.card       { border: 1px solid #333; border-radius: 8px;
              padding: 12px; margin: 6px 0; background: #1e1e1e; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════
#  SIDEBAR — Configuración
# ══════════════════════════════════════════

with st.sidebar:
    st.title("⚽ Sistema Apuestas")
    st.caption(f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    st.divider()

    bankroll     = st.number_input("💰 Bankroll ($)", value=100.0, step=10.0)
    max_apuesta  = st.number_input("Max apuesta ($)", value=5.0, step=1.0)
    usar_ia      = st.checkbox("🤖 Usar OpenAI", value=True)
    usar_noticias= st.checkbox("📰 Buscar noticias", value=True)
    enviar_tg    = st.checkbox("📲 Enviar a Telegram al analizar", value=False)

    st.divider()
    st.caption("Partidos seleccionados:")
    if "seleccionados" in st.session_state:
        n = len(st.session_state.seleccionados)
        st.metric("Seleccionados", n)
    else:
        st.metric("Seleccionados", 0)

    st.divider()
    if st.button("🔄 Recargar partidos", use_container_width=True):
        for key in ["df_partidos", "mercados", "sentimientos",
                    "analisis_ia", "df_analisis", "seleccionados"]:
            st.session_state.pop(key, None)
        st.rerun()


# ══════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════

st.title("⚽ Panel de Análisis Deportivo")
tab_partidos, tab_analisis, tab_sistema = st.tabs(
    ["📋 Partidos de hoy", "🎯 Análisis y apuestas", "🔧 Estado del sistema"]
)


# ══════════════════════════════════════════
#  TAB 1: PARTIDOS
# ══════════════════════════════════════════

with tab_partidos:

    # ── Cargar partidos ──────────────────────────────────
    if "df_partidos" not in st.session_state:
        with st.spinner("Descargando partidos y cuotas en tiempo real…"):
            try:
                from data_collector import recolectar_datos
                df = recolectar_datos()
                st.session_state.df_partidos = df

                # Mercados adicionales
                from markets_collector import (
                    descargar_todos_mercados, recolectar_mercados, mercados_a_dict
                )
                from config import LIGAS_PERMITIDAS
                cache_liga = {lid: descargar_todos_mercados(lid) for lid in LIGAS_PERMITIDAS}
                mercados = []
                for _, row in df.iterrows():
                    lid = int(row.get("liga_id", 1))
                    pm  = recolectar_mercados(row.to_dict(), lid,
                                             eventos_cache=cache_liga.get(lid, []))
                    mercados.append(mercados_a_dict(pm))
                st.session_state.mercados = mercados

            except Exception as e:
                st.error(f"Error al cargar partidos: {e}")
                st.stop()

    df = st.session_state.df_partidos

    if df.empty:
        st.warning("No hay partidos próximos para las ligas activas.")
        st.info("Comprueba que `LIGAS_PERMITIDAS` en config.py tiene ligas con partidos hoy.")
        st.stop()

    st.success(f"✅ {len(df)} partidos encontrados")

    # ── Noticias (si activadas) ──────────────────────────
    if usar_noticias and "sentimientos" not in st.session_state:
        with st.spinner("Buscando noticias de última hora…"):
            from sentiment_analyzer import analizar_sentimiento
            sentimientos = {}
            liga_nombre = df.iloc[0].get("liga_nombre", "") if not df.empty else ""
            for _, row in df.iterrows():
                key = f"{row.get('equipo_local','')} vs {row.get('equipo_visitante','')}"
                sent = analizar_sentimiento(
                    row.get("equipo_local", ""),
                    row.get("equipo_visitante", ""),
                    liga=liga_nombre,
                    usar_ia=False,
                )
                sentimientos[key] = sent
            st.session_state.sentimientos = sentimientos

    sentimientos = st.session_state.get("sentimientos", {})

    # ── Inicializar seleccionados ────────────────────────
    if "seleccionados" not in st.session_state:
        st.session_state.seleccionados = {}

    # ── Tarjeta por partido ──────────────────────────────
    st.subheader("Selecciona los partidos a analizar:")

    for i, row in df.iterrows():
        partido_key = f"{row.get('equipo_local','')} vs {row.get('equipo_visitante','')}"
        sent = sentimientos.get(partido_key)

        with st.container():
            col_check, col_info, col_odds, col_news = st.columns([0.5, 3, 2.5, 3])

            with col_check:
                checked = st.checkbox(
                    "",
                    key=f"sel_{i}",
                    value=st.session_state.seleccionados.get(partido_key, False),
                )
                st.session_state.seleccionados[partido_key] = checked

            with col_info:
                hora = row.get("hora", "")
                st.markdown(f"**{partido_key}**")
                st.caption(f"{row.get('liga_nombre','')} · {hora}")

            with col_odds:
                p_l = row.get("cons_prob_local",  0) or 0
                p_e = row.get("cons_prob_empate", 0) or 0
                p_v = row.get("cons_prob_visita", 0) or 0
                c_l = row.get("cuota_best_local", "-")
                c_v = row.get("cuota_best_visita", "-")

                def _color(p):
                    if p >= 0.65: return "prob-high"
                    if p >= 0.50: return "prob-med"
                    return "prob-low"

                st.markdown(
                    f"<span class='{_color(p_l)}'>{p_l:.0%}</span> "
                    f"@{c_l} &nbsp;|&nbsp; "
                    f"<span class='{_color(p_e)}'>{p_e:.0%}</span> X &nbsp;|&nbsp; "
                    f"<span class='{_color(p_v)}'>{p_v:.0%}</span> @{c_v}",
                    unsafe_allow_html=True,
                )
                st.caption(f"{int(row.get('n_bookmakers',0))} bookmakers")

            with col_news:
                if sent and sent.disponible:
                    if sent.alertas:
                        st.warning(f"⚠️ {len(sent.alertas)} alerta(s)")
                        with st.expander("Ver"):
                            for a in sent.alertas[:2]:
                                st.caption(a[:120])
                    else:
                        st.success("Sin alertas")
                else:
                    st.caption("Sin noticias")

        st.divider()

    # ── Botón analizar ───────────────────────────────────
    seleccionados_ids = [k for k, v in st.session_state.seleccionados.items() if v]
    n_sel = len(seleccionados_ids)

    if n_sel == 0:
        st.info("Selecciona al menos un partido para analizar.")
    else:
        if st.button(f"🎯 Analizar {n_sel} partido(s) seleccionado(s)",
                     type="primary", use_container_width=True):

            df_sel     = df[df.apply(
                lambda r: f"{r.get('equipo_local','')} vs {r.get('equipo_visitante','')}"
                           in seleccionados_ids, axis=1
            )].copy()
            merc_sel   = [
                m for row_idx, m in zip(range(len(df)), st.session_state.mercados)
                if df.iloc[row_idx].apply(
                    lambda r: f"{df.iloc[row_idx].get('equipo_local','')} vs "
                              f"{df.iloc[row_idx].get('equipo_visitante','')}"
                ).values[0] in seleccionados_ids
            ]

            # Reconstruir el subconjunto de mercados en orden correcto
            mercados_todos = st.session_state.mercados
            merc_sel = []
            for idx, (_, row) in enumerate(df.iterrows()):
                key = f"{row.get('equipo_local','')} vs {row.get('equipo_visitante','')}"
                if key in seleccionados_ids:
                    merc_sel.append(mercados_todos[idx])

            with st.spinner("Analizando con SHARP + OpenAI…"):
                from value_analyzer import analizar_todos
                df_analisis = analizar_todos(df_sel, bankroll=bankroll)
                st.session_state.df_analisis = df_analisis

                if usar_ia and os.getenv("OPENAI_API_KEY", ""):
                    from ai_analyzer import analizar_con_ia, formatear_analisis_ia

                    noticias_ctx = {}
                    if usar_noticias:
                        from sentiment_analyzer import resumen_para_prompt
                        for key in seleccionados_ids:
                            sent = sentimientos.get(key)
                            if sent and sent.disponible:
                                noticias_ctx[key] = resumen_para_prompt(sent)

                    analisis_ia = analizar_con_ia(
                        df_sel.to_dict("records"),
                        merc_sel,
                        bankroll=bankroll,
                        max_apuesta=max_apuesta,
                        noticias_por_partido=noticias_ctx,
                    )
                    st.session_state.analisis_ia = analisis_ia
                    msg = formatear_analisis_ia(analisis_ia)
                    st.session_state.msg_telegram = msg

                    if enviar_tg:
                        from telegram_bot import enviar_mensaje
                        enviar_mensaje(msg)
                        st.success("✅ Enviado a Telegram")

            st.success("Análisis completado. Ve a la pestaña **Análisis y apuestas**.")


# ══════════════════════════════════════════
#  TAB 2: ANÁLISIS
# ══════════════════════════════════════════

with tab_analisis:

    if "analisis_ia" not in st.session_state and "df_analisis" not in st.session_state:
        st.info("Primero ve a **Partidos de hoy**, selecciona partidos y haz clic en Analizar.")
        st.stop()

    # ── Sharp ────────────────────────────────────────────
    if "df_analisis" in st.session_state:
        df_res = st.session_state.df_analisis
        apostar   = df_res[df_res["recomendacion"] == "APOSTAR"]
        considerar= df_res[df_res["recomendacion"] == "CONSIDERAR"]

        col1, col2, col3 = st.columns(3)
        col1.metric("APOSTAR", len(apostar))
        col2.metric("CONSIDERAR", len(considerar))
        col3.metric("Total partidos", len(df_res))

    # ── IA ───────────────────────────────────────────────
    if "analisis_ia" in st.session_state:
        analisis = st.session_state.analisis_ia

        if "error" in analisis:
            st.error(f"Error IA: {analisis['error']}")
        else:
            # Individuales
            individuales = analisis.get("apuestas_individuales", [])
            if individuales:
                st.subheader("📌 Apuestas fijas")
                for ap in individuales:
                    prob = float(ap.get("prob_estimada", 0))
                    with st.container():
                        c1, c2, c3, c4 = st.columns([3, 2, 1.5, 1.5])
                        c1.markdown(f"**{ap.get('partido','')}**  \n{ap.get('descripcion','')}")
                        c1.caption(ap.get("razon", ""))
                        c2.markdown(f"Cuota: **{ap.get('cuota','')}**")
                        color = "prob-high" if prob >= 0.65 else "prob-med"
                        c3.markdown(
                            f"<span class='{color}'>{prob:.0%}</span>",
                            unsafe_allow_html=True
                        )
                        c4.markdown(f"**${ap.get('apuesta_sugerida',0):.2f}**")
                    st.divider()
            else:
                st.info("Sin apuestas fijas con prob ≥ 60% hoy.")

            # Combinadas
            combinadas = analisis.get("combinadas", [])
            if combinadas:
                st.subheader("🎯 Combinadas")
                tipo_color = {"Segura": "🟢", "Equilibrada": "🔵",
                              "Mixta": "🟡", "Larga": "🟠"}
                for comb in combinadas:
                    tipo     = comb.get("tipo", "")
                    cuota_t  = comb.get("cuota_total", 0)
                    prob_t   = float(comb.get("prob_total", 0))
                    apuesta  = float(comb.get("apuesta_sugerida", 0))
                    ganancia = round(apuesta * (float(cuota_t) - 1), 2) if cuota_t else 0

                    with st.expander(
                        f"{tipo_color.get(tipo,'⚪')} {tipo}  —  "
                        f"cuota {cuota_t}  |  prob {prob_t:.0%}  |  "
                        f"ganarías ${ganancia:.2f}"
                    ):
                        for p in comb.get("patas", []):
                            st.markdown(
                                f"- **{p.get('partido','')}** → "
                                f"{p.get('seleccion','')}  "
                                f"@ {p.get('cuota','')}  "
                                f"({float(p.get('prob',0)):.0%})"
                            )
                        st.caption(comb.get("razon", ""))
                        st.caption(f"Apostar: ${apuesta:.2f}")

            # Noticias
            noticias = analisis.get("_noticias_resumen", "")
            if noticias:
                st.subheader("📰 Noticias pre-partido")
                st.markdown(noticias)

            # Botón Telegram
            st.divider()
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("📲 Enviar mensaje a Telegram", type="primary"):
                    msg = st.session_state.get("msg_telegram", "")
                    if msg:
                        from telegram_bot import enviar_mensaje
                        enviar_mensaje(msg)
                        st.success("✅ Mensaje enviado a Telegram")
                    else:
                        st.warning("Sin mensaje generado aún.")
            with col_b:
                if "msg_telegram" in st.session_state:
                    with st.expander("Ver mensaje Telegram"):
                        st.text(st.session_state.msg_telegram)


# ══════════════════════════════════════════
#  TAB 3: SISTEMA
# ══════════════════════════════════════════

with tab_sistema:
    st.subheader("🔧 Estado de APIs y configuración")

    import requests

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**The Odds API**")
        try:
            r = requests.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": os.getenv("THE_ODDS_API_KEY", "")},
                timeout=5
            )
            rem   = r.headers.get("x-requests-remaining", "?")
            usado = r.headers.get("x-requests-used", "?")
            st.success(f"✅ Conectada")
            st.metric("Requests restantes", rem)
            st.metric("Requests usados (mes)", usado)
        except Exception as e:
            st.error(f"❌ {e}")

    with col2:
        st.markdown("**API-Football**")
        try:
            r = requests.get(
                "https://v3.football.api-sports.io/status",
                headers={"x-apisports-key": os.getenv("API_FOOTBALL_KEY", "")},
                timeout=5
            )
            d     = r.json().get("response", {})
            reqs  = d.get("requests", {})
            usado = reqs.get("current", "?")
            limite= reqs.get("limit_day", "?")
            st.success("✅ Conectada")
            st.metric("Requests hoy", f"{usado} / {limite}")
        except Exception as e:
            st.error(f"❌ {e}")

    st.divider()

    c1, c2, c3 = st.columns(3)
    c1.metric("OpenAI", "✅" if os.getenv("OPENAI_API_KEY") else "❌ Sin key")
    c2.metric("OpenWeather", "✅" if os.getenv("OPENWEATHER_KEY") else "⚠️ Opcional")
    c3.metric("Telegram", "✅" if os.getenv("TELEGRAM_TOKEN") else "❌ Sin token")

    st.divider()
    st.subheader("⚙️ Configuración activa")
    from config import LIGAS, LIGAS_PERMITIDAS, BANKROLL_INICIAL, OPENAI_MODEL
    st.json({
        "ligas_activas": [LIGAS.get(lid, lid) for lid in LIGAS_PERMITIDAS],
        "bankroll": BANKROLL_INICIAL,
        "openai_model": OPENAI_MODEL,
    })

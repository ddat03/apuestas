# ============================================================
#  dashboard_partido.py — Dashboard web para analizar_partido.py
#
#  Creado por Diego Aleman.
#
#  Iniciar:
#    streamlit run dashboard_partido.py
#  (o doble clic en el acceso directo del escritorio)
#
#  Cuotas reales (1xbet + Ecuabet) + estadísticas reales (Sofascore)
#  en un mismo lugar. Cada cuota tiene un checkbox al lado: marcarla
#  la guarda en el carrito (persiste en SQLite, no se pierde si
#  cerrás el navegador). El botón "Analizar" al final de cada
#  partido cruza lo que tengas marcado ACÁ contra la estadística
#  disponible — nunca inventa un análisis si no hay dato real para
#  cruzar. Arriba de todo, "Mi combinada" junta las patas de
#  DISTINTOS partidos que vayas eligiendo, con la cuota total, y las
#  guarda como combinada con un estado (pendiente/usada/descartada)
#  para que después puedas ver cuáles realmente jugaste.
# ============================================================

import pandas as pd
import streamlit as st

import analizar_partido as ap
import combos_manual_db as cm
import motor_analisis as ma
import sofascore_client as sc

st.set_page_config(page_title="Analizar Partido", page_icon="⚽", layout="wide")

st.markdown(
    "<span style='color:gray;font-size:0.8em'>Creado por Diego Aleman</span>",
    unsafe_allow_html=True,
)
st.title("⚽ Analizar Partido")
st.caption("Cuotas reales de 1xbet + Ecuabet, forma/posición/ausencias/H2H/historial de Sofascore. "
           "No aposta nada solo — solo muestra datos.")


@st.cache_data(ttl=300, show_spinner="Cargando partidos de 1xbet y Ecuabet (cobertura completa, ~30s)...")
def _partidos_cache():
    return ap.get_partidos()


@st.cache_data(ttl=300, show_spinner=False)
def _stats_cache(nombre: str):
    return ap.get_stats_equipo(nombre)


@st.cache_data(ttl=300, show_spinner=False)
def _h2h_cache(home: str, away: str, start_ts: int):
    return ap.get_h2h(home, away, start_ts)


@st.cache_data(ttl=300, show_spinner=False)
def _ausencias_cache(home: str, away: str, start_ts: int):
    home_id = sc.buscar_equipo_id(home)
    if not home_id:
        return None
    from datetime import datetime, timezone
    commence_iso = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
    evento = sc.buscar_evento_proximo(home_id, away, commence_iso)
    return sc.ausencias_equipo(evento["id"]) if evento else None


def _sync_checkbox(key: str, pick: dict) -> None:
    """Callback on_change: para cuando esto corre, Streamlit YA
    confirmó el nuevo valor del widget en session_state — nada de
    comparar contra el valor previo a mano ni de forzar un rerun,
    Streamlit rerunea solo después de un on_change (así es como
    está pensado que se use)."""
    if st.session_state[key]:
        cm.agregar_a_carrito(pick)
    else:
        cm.quitar_de_carrito(pick)


def _fila_pick(partido_label: str, liga: str, casa: str, mercado: str, seleccion: str, cuota,
               picks_analizar: list) -> None:
    """Dos checkboxes por selección, independientes:
      💾 Guardar  → agrega/saca del carrito persistente (SQLite) — para
                    ir armando la combinada.
      🔍 Analizar → solo marca esta pata para el botón "Analizar" de
                    más abajo, en esta misma carga de página — no toca
                    el carrito. Antes era el mismo checkbox para las
                    dos cosas, pero guardar una pata y querer analizarla
                    son decisiones distintas (una es "la quiero jugar",
                    la otra es "quiero ver si conviene")."""
    if cuota is None:
        return
    pick = {"partido": partido_label, "liga": liga, "casa": casa, "mercado": mercado,
            "seleccion": seleccion, "cuota": float(cuota)}
    base_key = f"{partido_label}::{casa}::{mercado}::{seleccion}"
    key_guardar = f"guardar::{base_key}"
    key_analizar = f"analizar::{base_key}"

    ya_estaba = cm.esta_en_carrito(pick)
    c1, c2, c3 = st.columns([0.12, 0.12, 0.76])
    c1.checkbox("💾", value=ya_estaba, key=key_guardar, on_change=_sync_checkbox,
               args=(key_guardar, pick), help="Guardar en Mi combinada")
    marcado_analizar = c2.checkbox("🔍", key=key_analizar, help="Marcar para analizar")
    c3.write(f"{seleccion} — **{cuota}**")

    if marcado_analizar:
        picks_analizar.append(pick)


# ────────────────────────────────────────────────────────────
#  Mi combinada (carrito persistente entre partidos)
# ────────────────────────────────────────────────────────────

with st.expander("🧺 Mi combinada", expanded=True):
    carrito = cm.obtener_carrito()
    if carrito:
        for p in carrito:
            col_txt, col_borrar = st.columns([6, 1])
            col_txt.write(f"{p['partido']} — {p['casa']} — {p['mercado']}: **{p['seleccion']}** ({p['cuota']})")
            if col_borrar.button("❌", key=f"borrar_carrito_{p['id']}"):
                cm.quitar_de_carrito_por_id(p["id"])
                st.rerun()
        st.markdown(f"**Cuota total: {cm.cuota_total_carrito()}**  ({len(carrito)} patas)")
        col_g, col_v = st.columns(2)
        if col_g.button("💾 Guardar como combinada"):
            cm.guardar_combinada()
            st.rerun()
        if col_v.button("🗑️ Vaciar carrito"):
            cm.vaciar_carrito()
            st.rerun()
    else:
        st.caption("Vacío — marcá selecciones más abajo para ir armando una combinada.")

    guardadas = cm.listar_combinadas()
    if guardadas:
        st.markdown("**Combinadas guardadas**")
        for combo in guardadas:
            texto_patas = " + ".join(f"{p['seleccion']} ({p['cuota']})" for p in combo["patas"])
            col_txt, col_estado = st.columns([4, 1])
            col_txt.write(f"#{combo['id']} — cuota total **{combo['cuota_total']}** — {texto_patas}")
            estado = col_estado.radio(
                "estado", ["pendiente", "usada", "descartada"],
                index=["pendiente", "usada", "descartada"].index(combo["estado"]),
                key=f"estado_combo_{combo['id']}", horizontal=True, label_visibility="collapsed",
            )
            if estado != combo["estado"]:
                cm.marcar_combinada(combo["id"], estado)
                st.rerun()

st.divider()

# ────────────────────────────────────────────────────────────
#  Selección de partido
# ────────────────────────────────────────────────────────────

col_boton, _ = st.columns([1, 5])
if col_boton.button("🔄 Recargar partidos"):
    _partidos_cache.clear()

partidos = _partidos_cache()

opciones = {
    f"{p['home']} vs {p['away']}  ({p['liga']})" + ("  [1xbet+Ecuabet]" if p["raw_ec"] else "  [solo 1xbet]"): p
    for p in partidos
}
etiqueta = st.selectbox("Elegí un partido", list(opciones.keys()))
elegido = opciones[etiqueta]
partido_label = f"{elegido['home']} vs {elegido['away']}"

st.divider()

# ── Cuotas ────────────────────────────────────────────────
col_1x, col_ec = st.columns(2)
picks_analizar: list = []   # patas marcadas con 🔍 en ESTA carga de página

with col_1x:
    st.subheader("1xbet")
    mercados = ap.get_mercados_1xbet(elegido["raw_1x"])
    for nombre, contenido in mercados.items():
        if nombre == "_sin_identificar":
            continue
        st.markdown(f"**{nombre}**")
        if isinstance(contenido, dict):
            for opcion, cuota in contenido.items():
                _fila_pick(partido_label, elegido["liga"], "1xbet", nombre, opcion, cuota, picks_analizar)
        else:
            for fila in contenido:
                linea = fila["linea"]
                for opcion, cuota in fila.items():
                    if opcion == "linea":
                        continue
                    _fila_pick(partido_label, elegido["liga"], "1xbet", nombre, f"{opcion} {linea}", cuota, picks_analizar)
    if mercados.get("_sin_identificar"):
        st.caption(f"+{len(mercados['_sin_identificar'])} mercado(s) más sin identificar "
                   f"(códigos: {mercados['_sin_identificar']})")

with col_ec:
    st.subheader("Ecuabet")
    mercados_ec = ap.get_mercados_ecuabet(elegido["raw_ec"], elegido["ecuabet_ctx"])
    if mercados_ec:
        for m in mercados_ec:
            st.markdown(f"**{m['mercado']}**")
            for opcion, cuota in m["opciones"].items():
                _fila_pick(partido_label, elegido["liga"], "ecuabet", m["mercado"], opcion, cuota, picks_analizar)
    else:
        st.info("Ecuabet no tiene este partido en su listado de próximos ahora mismo.")

    st.caption("Mercados de corners/tarjetas/jugador existen en la web de Ecuabet pero todavía no se "
               "pudieron automatizar (pestaña detrás de un iframe) — si los ves ahí, agregalos con "
               "\"➕ Agregar manual\" más abajo.")

with st.expander("➕ Agregar manual (para lo que no sale arriba, ej. corners/tarjetas/jugador de Ecuabet)"):
    with st.form("form_manual", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        m_mercado = c1.text_input("Mercado", placeholder="Tiros esquina")
        m_seleccion = c2.text_input("Selección", placeholder="Más de 9.5")
        m_cuota = c3.number_input("Cuota", min_value=1.01, step=0.01, value=1.90)
        m_alcance_label = st.radio(
            "¿De qué equipo es esta apuesta? (necesario para poder analizarla bien — "
            "no es lo mismo \"Newcastle más de 5.5 corners\" que \"el partido entero más de 10.5\")",
            [f"{elegido['home']} (local)", f"{elegido['away']} (visita)", "General / total del partido"],
            horizontal=True,
        )
        m_tambien_analizar = st.checkbox("También marcarla para analizar ahora", value=True)
        if st.form_submit_button("Agregar al carrito"):
            m_equipo = ("home" if m_alcance_label.startswith(elegido["home"]) else
                       "away" if m_alcance_label.startswith(elegido["away"]) else "total")
            pick_manual = {"partido": partido_label, "liga": elegido["liga"], "casa": "manual",
                          "mercado": m_mercado or "Manual", "seleccion": m_seleccion, "cuota": m_cuota,
                          "equipo": m_equipo}
            cm.agregar_a_carrito(pick_manual)
            if m_tambien_analizar:
                picks_analizar.append(pick_manual)

st.divider()

# ── Estadísticas ──────────────────────────────────────────
st.subheader(f"Estadísticas (Sofascore, últimos {ap.N_HISTORIAL} partidos)")
col_home, col_away = st.columns(2)
stats_por_lado = {}

for col, lado, nombre in ((col_home, "home", elegido["home"]), (col_away, "away", elegido["away"])):
    with col:
        st.markdown(f"#### {nombre}")
        stats = _stats_cache(nombre)
        stats_por_lado[lado] = stats
        if not stats["equipo_id"]:
            st.warning("No encontrado en Sofascore")
            continue

        forma = stats["forma"]
        st.metric("Forma", forma.forma_str or "N/A",
                  f"{forma.goles_favor:.1f} GF / {forma.goles_contra:.1f} GC por partido")

        pos = stats["posicion"]
        if pos:
            st.caption(f"Tabla: **{pos['posicion']}°/{pos['total_equipos']}** — {pos['puntos']} pts "
                       f"({pos['ganados']}G-{pos['empatados']}E-{pos['perdidos']}P en {pos['jugados']})")

        historial = stats["historial"]
        if historial:
            filas = []
            for p in historial:
                af, ec = p["a_favor"], p["en_contra"]
                filas.append({
                    "Rival": p["rival"],
                    "Goles (F-C)": f"{p.get('goles_favor','?')}-{p.get('goles_contra','?')}",
                    "Corners (F-C)": f"{af.get('corners','?')}-{ec.get('corners','?')}",
                    "Amarillas": af.get("amarillas"),
                    "Rojas": af.get("rojas"),
                    "Tiros": af.get("tiros_totales"),
                    "Tiros al arco": af.get("tiros_arco"),
                    "Posesión %": af.get("posesion"),
                    "xG": af.get("goles_esperados"),
                })
            st.dataframe(pd.DataFrame(filas), hide_index=True, use_container_width=True)
        else:
            st.caption("Sin historial de estadísticas disponible")

ausencias = _ausencias_cache(elegido["home"], elegido["away"], elegido["start_ts"])
if ausencias and (ausencias["home"] or ausencias["away"]):
    st.markdown("#### ⚠️ Ausencias / lesionados")
    col_ah, col_aa = st.columns(2)
    for col, lado in ((col_ah, "home"), (col_aa, "away")):
        with col:
            if ausencias[lado]:
                for a in ausencias[lado]:
                    vuelve = f" (vuelve ~{a['vuelve'][:10]})" if a.get("vuelve") else ""
                    st.write(f"- **{a['nombre']}** — {a['motivo']}{vuelve}")
            else:
                st.caption("Sin bajas reportadas")

st.divider()

# ── H2H ───────────────────────────────────────────────────
st.subheader("Head to Head")
h2h = _h2h_cache(elegido["home"], elegido["away"], elegido["start_ts"])
if h2h and h2h.partidos_totales:
    st.write(f"{h2h.resumen} — **Domina: {h2h.domina}**")
else:
    st.caption("Partido no encontrado en el calendario de Sofascore para el H2H")

st.divider()

# ── Analizar ──────────────────────────────────────────────
st.subheader("🔍 Analizar selecciones de este partido")
st.caption("Marcá 🔍 en las cuotas que te interesan (arriba) — es independiente de 💾 Guardar, "
          "podés analizar sin guardar y guardar sin analizar.")
picks_este_partido = picks_analizar

if not picks_este_partido:
    st.caption("Marcá alguna selección con 🔍 arriba para poder analizarla.")
elif st.button("Analizar", type="primary"):
    home_stats = stats_por_lado.get("home", {})
    away_stats = stats_por_lado.get("away", {})
    if not home_stats.get("equipo_id") or not away_stats.get("equipo_id"):
        st.warning("Faltan datos de Sofascore de alguno de los dos equipos — no se puede analizar.")
    else:
        contexto = {
            "home": elegido["home"], "away": elegido["away"],
            "forma_home": home_stats["forma"], "forma_away": away_stats["forma"],
            "hist_home": home_stats["historial"], "hist_away": away_stats["historial"],
            "ausencias": ausencias,
        }
        for p in picks_este_partido:
            v = ma.analizar_pick(p, contexto)
            with st.container(border=True):
                st.markdown(f"**{p['mercado']} — {p['seleccion']}** ({p['casa']}, cuota {p['cuota']})")
                if v.analizable:
                    st.write(v.resumen)
                    if v.detalle:
                        st.caption(v.detalle)
                else:
                    st.info(v.resumen)

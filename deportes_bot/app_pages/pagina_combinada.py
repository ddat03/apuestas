# ============================================================
#  app_pages/pagina_combinada.py — Pestaña "Combinada PrimaTips"
#
#  Creado por Diego Aleman.
#
#  Toma los tips de PrimaTips (columna T) de una fecha o RANGO de
#  fechas, se queda con los de cuota baja (O <= umbral), y cruza cada
#  uno con el feed de Ecuabet: si el partido está y Ecuabet tiene el
#  mercado, saca la cuota de Ecuabet para ESA selección. Al final: la
#  combinada de todas (o las que dejes tildadas) con su cuota total,
#  y un botón para analizar las tildadas contra Sofascore.
#
#  No decide nada: PrimaTips ya eligió el tip, Ecuabet pone el precio.
# ============================================================

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import analizar_partido as ap
import combos_manual_db as cm
import motor_analisis as ma
import primatips_combos as pc
import sofascore_client as sc


def _producto(valores) -> float:
    total = 1.0
    for v in valores:
        total *= (v or 1.0)
    return total


st.title("🎯 Combinada PrimaTips")
st.caption("Tips de PrimaTips (columna T) con cuota baja, cruzados con Ecuabet. "
           "La cuota total usa la de Ecuabet (donde jugás). Podés armar de un día o de varios.")


@st.cache_data(ttl=600, show_spinner="Bajando tips de PrimaTips...")
def _tips_cache(desde_iso: str, hasta_iso: str):
    return pc.scrapear_primatips_rango(desde_iso, hasta_iso)


@st.cache_data(ttl=600, show_spinner="Bajando feed de Ecuabet (~30s)...")
def _ecuabet_cache():
    return ap._cargar_ecuabet_raw()


@st.cache_data(ttl=60, show_spinner=False)
def _sofascore_ok_cache():
    """Ping corto cacheado 60s — para distinguir "Sofascore no responde
    ahora" (red/bloqueo temporal) de "no encontramos a ese equipo" antes
    de analizar 20-30 patas y que TODAS digan lo mismo sin explicar por
    qué."""
    return sc.probar_conexion()


@st.cache_data(ttl=600, show_spinner="Cruzando tips con Ecuabet...")
def _cruzadas_cache(desde_iso: str, hasta_iso: str, _tips, _ecuabet_ctx):
    """El cruce en sí (cruzar_con_ecuabet) es lo caro de esta página —
    recorre cientos de tips contra ~1900 partidos de Ecuabet. Sin este
    caché se repetía en CADA interacción de la página (tildar una
    casilla, mover el slider de cuota, etc.), no solo al cambiar de
    fecha. Los parámetros con "_" no entran en la clave del caché (así
    Streamlit no pierde tiempo hasheando listas/diccionarios enormes) —
    la clave real es solo el rango de fechas, que es lo que de verdad
    determina el resultado."""
    return pc.cruzar_con_ecuabet(_tips, _ecuabet_ctx, ap.get_mercados_ecuabet)


@st.cache_data(ttl=600, show_spinner=False)
def _stats_equipo_cache(nombre: str):
    return ap.get_stats_equipo(nombre)


@st.cache_data(ttl=600, show_spinner=False)
def _contexto_pata(home: str, away: str, fecha_iso: str) -> dict | None:
    # Cacheado por equipo (no solo por el partido completo) — un mismo
    # equipo puede aparecer en varias patas tildadas de días distintos,
    # y sin esto se le pedía la forma/historial a Sofascore de nuevo cada
    # vez en lugar de reusar lo ya traído.
    sh = _stats_equipo_cache(home)
    sa = _stats_equipo_cache(away)
    if not sh["equipo_id"] or not sa["equipo_id"]:
        return None
    ev = sc.buscar_evento_proximo(sh["equipo_id"], away, f"{fecha_iso}T12:00:00Z", ventana_horas=48)
    ev_id = ev["id"] if ev else None
    return {
        "home": home, "away": away,
        "forma_home": sh["forma"], "forma_away": sa["forma"],
        "hist_home": sh["historial"], "hist_away": sa["historial"],
        "ausencias": sc.ausencias_equipo(ev_id) if ev_id else None,
        "odds_referencia": sc.odds_1x2_evento(ev_id) if ev_id else None,
        "calidad_home": sc.calidad_rivales_recientes(sh["equipo_id"], sh["historial"]),
        "calidad_away": sc.calidad_rivales_recientes(sa["equipo_id"], sa["historial"]),
    }


# ── Controles ─────────────────────────────────────────────
hoy = datetime.now(timezone.utc).date()
c1, c2 = st.columns([2, 2])
rango = c1.date_input(
    "Fecha o rango de fechas", value=(hoy, hoy),
    min_value=hoy - timedelta(days=3), max_value=hoy + timedelta(days=14),
    help="Elegí una fecha, o un rango (clic en dos días) para combinar varios días.",
)
umbral_min, umbral = c2.slider(
    "Rango de cuota por pata (O de PrimaTips)", 1.01, 2.00, (1.05, 1.30), 0.01,
    help="Los dos extremos se pueden mover — subí el mínimo si no querés las cuotas "
         "\"casi seguras\" de 1.01-1.04, que suman poco a la cuota total.",
)

# date_input con value de tupla devuelve (a,) mientras se elige el 2º día, (a,b) al terminar
if isinstance(rango, (tuple, list)):
    desde = rango[0] if rango else hoy
    hasta = rango[-1] if rango else hoy
else:
    desde = hasta = rango

if st.button("🔎 Buscar y cruzar con Ecuabet", type="primary"):
    st.session_state["pt_buscado"] = (desde.isoformat(), hasta.isoformat())

if "pt_buscado" not in st.session_state:
    st.info("Elegí fecha(s) y umbral, después tocá **Buscar y cruzar con Ecuabet**.")
    st.stop()

desde_iso, hasta_iso = st.session_state["pt_buscado"]
if (desde_iso, hasta_iso) != (desde.isoformat(), hasta.isoformat()):
    st.warning(f"Mostrando **{desde_iso}**" + (f" → **{hasta_iso}**" if hasta_iso != desde_iso else "")
               + ". Tocá **Buscar** de nuevo para aplicar el cambio de fechas.")

# ── Cruce ─────────────────────────────────────────────────
try:
    tips = _tips_cache(desde_iso, hasta_iso)
except Exception as e:
    st.error(f"No se pudo leer PrimaTips: {e}")
    st.stop()

if not tips:
    st.warning("PrimaTips no tiene tips para esas fechas todavía.")
    st.stop()

try:
    ecuabet_ctx = _ecuabet_cache()
except Exception as e:
    st.error(f"Ecuabet no respondió (suele ser un pico momentáneo del servidor). {type(e).__name__}")
    if st.button("🔄 Reintentar"):
        _ecuabet_cache.clear()
        st.rerun()
    st.stop()

cruzadas = _cruzadas_cache(desde_iso, hasta_iso, tips, ecuabet_ctx)
combo = pc.armar_combinada(cruzadas, umbral=umbral, umbral_min=umbral_min)

st.divider()

if not combo.patas:
    st.warning(f"Ningún tip con O entre {umbral_min} y {umbral} está en Ecuabet para esas fechas. "
               "Probá ampliar el rango o mirar 'No cruzaron' abajo.")
    st.stop()

rango_txt = f"{desde_iso}" + (f" → {hasta_iso}" if hasta_iso != desde_iso else "")
st.subheader(f"Patas candidatas ({len(combo.patas)}) · {rango_txt} — destildá las que no quieras")

# Checkbox al lado del encabezado "Incluir" para marcar/desmarcar todas
# de una — sin esto, con 20-30 patas destildar una por una es tedioso.
if "pt_editor_seed" not in st.session_state:
    st.session_state["pt_editor_seed"] = 0
c_chk, c_lbl = st.columns([0.05, 0.95])
incluir_default = c_chk.checkbox(
    "todas", value=True, key="pt_check_todas", label_visibility="collapsed",
    on_change=lambda: st.session_state.update(pt_editor_seed=st.session_state["pt_editor_seed"] + 1),
)
c_lbl.caption("☑️ Incluir — tildá/destildá acá para marcar o desmarcar TODAS las patas de una")

df = pd.DataFrame([{
    "Incluir": incluir_default,
    "Fecha": p.tip.fecha,
    "Partido": f"{p.tip.home} vs {p.tip.away}",
    "Liga": p.tip.liga,
    "Tip (T)": p.tip.tip,
    "Apuesta": p.tip_legible,
    "O PrimaTips": p.tip.odd,
    "Cuota Ecuabet": p.cuota_ecuabet,
    "Opción Ecuabet": p.seleccion_ecuabet,
} for p in combo.patas])

editado = st.data_editor(
    df, hide_index=True, width="stretch",
    column_config={
        "Incluir": st.column_config.CheckboxColumn(width="small"),
        "O PrimaTips": st.column_config.NumberColumn(format="%.2f"),
        "Cuota Ecuabet": st.column_config.NumberColumn(format="%.3f"),
    },
    disabled=[c for c in df.columns if c != "Incluir"],
    key=f"pt_editor_{st.session_state['pt_editor_seed']}",
)

incluidas = [combo.patas[i] for i, on in enumerate(editado["Incluir"].fillna(False).tolist()) if on]
total_ec = round(_producto(p.cuota_ecuabet for p in incluidas), 3) if incluidas else 0.0
total_pt = round(_producto(p.tip.odd for p in incluidas), 3) if incluidas else 0.0

m1, m2, m3 = st.columns(3)
m1.metric("Patas elegidas", len(incluidas))
m2.metric("Cuota total Ecuabet", f"{total_ec:.3f}")
m3.metric("Cuota total PrimaTips (ref.)", f"{total_pt:.3f}")

col_g, col_a = st.columns(2)

if incluidas and col_g.button("💾 Guardar como combinada", type="primary"):
    patas_db = [{
        "partido": f"{p.tip.home} vs {p.tip.away}",
        "liga": p.tip.liga,
        "casa": "ecuabet",
        "mercado": f"PrimaTips · {p.mercado_ecuabet} · {p.tip.tip}",
        "seleccion": f"{p.tip_legible}  [{p.seleccion_ecuabet}]",
        "cuota": p.cuota_ecuabet,
        "fecha": p.tip.fecha,
    } for p in incluidas]
    cid = cm.guardar_combinada_directa(patas_db, cuota_total=total_ec)
    st.success(f"Guardada como combinada #{cid}. Se liquida sola (✓/✗) al abrir la pestaña "
               "Analizar Partido cuando los partidos terminen.")

analizar = incluidas and col_a.button("🔬 Analizar las tildadas (Sofascore)")

# ── Análisis de las patas elegidas ────────────────────────
if analizar:
    st.divider()
    st.subheader("Análisis de las patas elegidas")
    if not _sofascore_ok_cache():
        st.warning("⚠️ Sofascore no está respondiendo ahora mismo (red o bloqueo temporal) — por eso las "
                  "patas de abajo van a salir todas como 'sin datos', no porque esos equipos no existan. "
                  "Probá de nuevo en unos minutos.")
        sc.limpiar_cache_equipos()
    st.caption("Tiros/corners/faltas recientes + calidad de rivales + cuota de referencia. "
               "No es una garantía — es lo mismo que hace la pestaña Analizar Partido, aplicado a cada pata.")
    barra = st.progress(0.0)
    for i, p in enumerate(incluidas, 1):
        barra.progress(i / len(incluidas))
        ctx = _contexto_pata(p.tip.home, p.tip.away, p.tip.fecha)
        with st.container(border=True):
            st.markdown(f"**{p.tip.home} vs {p.tip.away}** · {p.tip_legible} · "
                        f"Ecuabet {p.cuota_ecuabet}")
            if not ctx:
                st.info("Sofascore no tiene a uno de los dos equipos — no se puede analizar.")
                continue
            if p.tip.tip == "X":
                fh = ctx["forma_home"].forma_str or "N/A"
                fa = ctx["forma_away"].forma_str or "N/A"
                st.write(f"Empate — forma {p.tip.home}: {fh} · {p.tip.away}: {fa}. "
                         "El empate a cuota baja es raro; mirá bien esta pata.")
            else:
                if p.tip.tip in ("1", "2"):
                    seleccion = ctx["home"] if p.tip.tip == "1" else ctx["away"]
                    pick = {"mercado": "1x2", "seleccion": seleccion, "cuota": p.cuota_ecuabet, "equipo":
                            "home" if p.tip.tip == "1" else "away"}
                else:  # 1X / 12 / X2
                    pick = {"mercado": "Doble oportunidad", "seleccion": p.seleccion_ecuabet,
                            "cuota": p.cuota_ecuabet, "tip": p.tip.tip}
                v = ma.analizar_pick(pick, ctx)
                st.write(v.resumen)
                if v.detalle:
                    st.caption(v.detalle)

            # Sugerencias de OTRAS apuestas seguras (tiros/corners/faltas)
            # sobre este mismo partido — sin cuota, son para buscarlas
            # aparte en Ecuabet/1xbet si te interesan.
            sugerencias = (ma.sugerencias_seguras(p.tip.home, ctx["hist_home"]) +
                          ma.sugerencias_seguras(p.tip.away, ctx["hist_away"]))
            if sugerencias:
                with st.expander("💡 Otras apuestas a considerar de este partido (sin cuota)"):
                    for s in sugerencias:
                        st.caption(f"• {s}")
    barra.empty()

# ── Lo que no entró ───────────────────────────────────────
with st.expander(f"Tips O entre {umbral_min} y {umbral} que NO cruzaron con Ecuabet "
                 f"({len(combo.no_cruzadas_en_umbral)})"):
    if combo.no_cruzadas_en_umbral:
        st.caption("Se revisó nombre por nombre con alias/variantes conocidas antes de darlos por no "
                   "encontrados — si alguno de estos SÍ está en Ecuabet con otro nombre, avisá cuál.")
        st.dataframe(pd.DataFrame([{
            "Fecha": p.tip.fecha, "Partido": f"{p.tip.home} vs {p.tip.away}",
            "Liga": p.tip.liga, "Tip (T)": p.tip.tip, "O PrimaTips": p.tip.odd, "Motivo": p.motivo,
        } for p in combo.no_cruzadas_en_umbral]), hide_index=True, width="stretch")
    else:
        st.caption("Todos los tips en rango cruzaron.")

with st.expander(f"En Ecuabet pero con O fuera del rango [{umbral_min}, {umbral}] ({len(combo.fuera_umbral)})"):
    if combo.fuera_umbral:
        st.dataframe(pd.DataFrame([{
            "Fecha": p.tip.fecha, "Partido": f"{p.tip.home} vs {p.tip.away}",
            "Tip (T)": p.tip.tip, "O PrimaTips": p.tip.odd, "Cuota Ecuabet": p.cuota_ecuabet,
        } for p in combo.fuera_umbral]), hide_index=True, width="stretch")
    else:
        st.caption("Ninguno.")

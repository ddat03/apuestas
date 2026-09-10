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


@st.cache_data(ttl=600, show_spinner=False)
def _contexto_pata(home: str, away: str, fecha_iso: str) -> dict | None:
    sh = ap.get_stats_equipo(home)
    sa = ap.get_stats_equipo(away)
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
    }


# ── Controles ─────────────────────────────────────────────
hoy = datetime.now(timezone.utc).date()
c1, c2 = st.columns([2, 2])
rango = c1.date_input(
    "Fecha o rango de fechas", value=(hoy, hoy),
    min_value=hoy - timedelta(days=3), max_value=hoy + timedelta(days=14),
    help="Elegí una fecha, o un rango (clic en dos días) para combinar varios días.",
)
umbral = c2.slider("Cuota máx. por pata (O de PrimaTips)", 1.05, 2.00, 1.30, 0.01)

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

combo = pc.armar_combinada(
    pc.cruzar_con_ecuabet(tips, ecuabet_ctx, ap.get_mercados_ecuabet), umbral=umbral)

st.divider()

if not combo.patas:
    st.warning(f"Ningún tip con O ≤ {umbral} está en Ecuabet para esas fechas. "
               "Probá subir el umbral o mirar 'No cruzaron' abajo.")
    st.stop()

rango_txt = f"{desde_iso}" + (f" → {hasta_iso}" if hasta_iso != desde_iso else "")
st.subheader(f"Patas candidatas ({len(combo.patas)}) · {rango_txt} — destildá las que no quieras")

df = pd.DataFrame([{
    "Incluir": True,
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
    key="pt_editor",
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
    st.caption("Forma reciente + bajas + cuota de referencia. No es una garantía — "
               "es lo mismo que hace la pestaña Analizar Partido, aplicado a cada pata.")
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
                continue
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
    barra.empty()

# ── Lo que no entró ───────────────────────────────────────
with st.expander(f"Tips O ≤ {umbral} que NO cruzaron con Ecuabet ({len(combo.no_cruzadas_en_umbral)})"):
    if combo.no_cruzadas_en_umbral:
        st.dataframe(pd.DataFrame([{
            "Fecha": p.tip.fecha, "Partido": f"{p.tip.home} vs {p.tip.away}",
            "Liga": p.tip.liga, "Tip (T)": p.tip.tip, "O PrimaTips": p.tip.odd, "Motivo": p.motivo,
        } for p in combo.no_cruzadas_en_umbral]), hide_index=True, width="stretch")
    else:
        st.caption("Todos los tips en umbral cruzaron.")

with st.expander(f"En Ecuabet pero con O > {umbral} ({len(combo.fuera_umbral)})"):
    if combo.fuera_umbral:
        st.dataframe(pd.DataFrame([{
            "Fecha": p.tip.fecha, "Partido": f"{p.tip.home} vs {p.tip.away}",
            "Tip (T)": p.tip.tip, "O PrimaTips": p.tip.odd, "Cuota Ecuabet": p.cuota_ecuabet,
        } for p in combo.fuera_umbral]), hide_index=True, width="stretch")
    else:
        st.caption("Ninguno.")

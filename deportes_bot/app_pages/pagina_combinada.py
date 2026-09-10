# ============================================================
#  app_pages/pagina_combinada.py — Pestaña "Combinada PrimaTips"
#
#  Creado por Diego Aleman.
#
#  Toma los tips de PrimaTips (columna T) de una fecha, se queda con
#  los de cuota baja (O <= umbral), y cruza cada uno con el feed de
#  Ecuabet: si el partido está y Ecuabet tiene el mercado, saca la
#  cuota de Ecuabet para ESA selección. Al final: la combinada de
#  todas (o las que dejes tildadas) con su cuota total.
#
#  No decide nada: PrimaTips ya eligió el tip, Ecuabet pone el
#  precio. Esto solo cruza y multiplica.
# ============================================================

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import analizar_partido as ap
import combos_manual_db as cm
import primatips_combos as pc


def _producto(valores) -> float:
    total = 1.0
    for v in valores:
        total *= (v or 1.0)
    return total

st.title("🎯 Combinada PrimaTips")
st.caption("Tips de PrimaTips (columna T) con cuota baja, cruzados con Ecuabet. "
           "La cuota total usa la de Ecuabet (donde jugás).")


@st.cache_data(ttl=600, show_spinner="Bajando tips de PrimaTips...")
def _tips_cache(fecha_iso: str):
    return pc.scrapear_primatips(fecha_iso)


@st.cache_data(ttl=600, show_spinner="Bajando feed de Ecuabet (~30s)...")
def _ecuabet_cache():
    return ap._cargar_ecuabet_raw()


# ── Controles ─────────────────────────────────────────────
hoy = datetime.now(timezone.utc).date()
c1, c2, c3 = st.columns([1.2, 1.2, 2])
fecha = c1.date_input("Fecha", value=hoy, min_value=hoy - timedelta(days=2),
                      max_value=hoy + timedelta(days=10))
umbral = c2.slider("Cuota máx. por pata (O de PrimaTips)", 1.05, 2.00, 1.30, 0.01)

if c3.button("🔎 Buscar y cruzar con Ecuabet", type="primary"):
    st.session_state["pt_buscado"] = fecha.isoformat()

if "pt_buscado" not in st.session_state:
    st.info("Elegí fecha y umbral, después tocá **Buscar y cruzar con Ecuabet**.")
    st.stop()

fecha_iso = st.session_state["pt_buscado"]
if fecha_iso != fecha.isoformat():
    st.warning(f"Mostrando resultados de **{fecha_iso}**. Tocá **Buscar** de nuevo para cambiar a {fecha.isoformat()}.")

# ── Cruce ─────────────────────────────────────────────────
try:
    tips = _tips_cache(fecha_iso)
except Exception as e:
    st.error(f"No se pudo leer PrimaTips: {e}")
    st.stop()

if not tips:
    st.warning(f"PrimaTips no tiene tips para {fecha_iso} todavía.")
    st.stop()

try:
    ecuabet_ctx = _ecuabet_cache()
except Exception as e:
    st.error(f"Ecuabet no respondió (suele ser un pico momentáneo del servidor). {type(e).__name__}")
    if st.button("🔄 Reintentar"):
        _ecuabet_cache.clear()
        st.rerun()
    st.stop()

cruzadas = pc.cruzar_con_ecuabet(tips, fecha_iso, ecuabet_ctx, ap.get_mercados_ecuabet)
combo = pc.armar_combinada(cruzadas, umbral=umbral)

st.divider()

if not combo.patas:
    st.warning(f"Ningún tip con O ≤ {umbral} está en Ecuabet para esta fecha. "
               "Probá subir el umbral o mirar 'No cruzadas' abajo.")
else:
    st.subheader(f"Patas candidatas ({len(combo.patas)}) — destildá las que no quieras")

    df = pd.DataFrame([{
        "Incluir": True,
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
        disabled=["Partido", "Liga", "Tip (T)", "Apuesta", "O PrimaTips", "Cuota Ecuabet", "Opción Ecuabet"],
        key="pt_editor",
    )

    incluidas = [combo.patas[i] for i, on in enumerate(editado["Incluir"].fillna(False).tolist()) if on]
    total_ec = round(_producto(p.cuota_ecuabet for p in incluidas), 3) if incluidas else 0.0
    total_pt = round(_producto(p.tip.odd for p in incluidas), 3) if incluidas else 0.0

    m1, m2, m3 = st.columns(3)
    m1.metric("Patas elegidas", len(incluidas))
    m2.metric("Cuota total Ecuabet", f"{total_ec:.3f}")
    m3.metric("Cuota total PrimaTips (ref.)", f"{total_pt:.3f}")

    if incluidas and st.button("💾 Guardar como combinada", type="primary"):
        patas_db = [{
            "partido": f"{p.tip.home} vs {p.tip.away}",
            "liga": p.tip.liga,
            "casa": "ecuabet",
            "mercado": f"PrimaTips · {p.mercado_ecuabet}",
            "seleccion": f"{p.tip_legible}  [{p.seleccion_ecuabet}]",
            "cuota": p.cuota_ecuabet,
        } for p in incluidas]
        cid = cm.guardar_combinada_directa(patas_db, cuota_total=total_ec)
        st.success(f"Guardada como combinada #{cid} (estado: pendiente). "
                   "La ves en la pestaña Analizar Partido → 'Mi combinada' → Combinadas guardadas.")

# ── Lo que no entró ───────────────────────────────────────
with st.expander(f"Tips O ≤ {umbral} que NO cruzaron con Ecuabet ({len(combo.no_cruzadas_en_umbral)})"):
    if combo.no_cruzadas_en_umbral:
        st.dataframe(pd.DataFrame([{
            "Partido": f"{p.tip.home} vs {p.tip.away}",
            "Liga": p.tip.liga,
            "Tip (T)": p.tip.tip,
            "O PrimaTips": p.tip.odd,
            "Motivo": p.motivo,
        } for p in combo.no_cruzadas_en_umbral]), hide_index=True, width="stretch")
    else:
        st.caption("Todos los tips en umbral cruzaron.")

with st.expander(f"En Ecuabet pero con O > {umbral} ({len(combo.fuera_umbral)})"):
    if combo.fuera_umbral:
        st.dataframe(pd.DataFrame([{
            "Partido": f"{p.tip.home} vs {p.tip.away}",
            "Tip (T)": p.tip.tip,
            "O PrimaTips": p.tip.odd,
            "Cuota Ecuabet": p.cuota_ecuabet,
        } for p in combo.fuera_umbral]), hide_index=True, width="stretch")
    else:
        st.caption("Ninguno.")

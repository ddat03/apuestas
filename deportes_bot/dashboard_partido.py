# ============================================================
#  dashboard_partido.py — Entry point del dashboard (multipágina)
#
#  Creado por Diego Aleman.
#
#  Iniciar:
#    streamlit run dashboard_partido.py
#  (o doble clic en el acceso directo del escritorio)
#
#  Este archivo es fino a propósito: solo config + navegación. El
#  contenido vive en app_pages/:
#    - pagina_partido.py   → analizar UN partido (cuotas + stats)
#    - pagina_combinada.py → armar combinada cruzando PrimaTips + Ecuabet
# ============================================================

import streamlit as st

st.set_page_config(page_title="Apuestas — Diego", page_icon="⚽", layout="wide")

st.markdown(
    "<span style='color:gray;font-size:0.8em'>Creado por Diego Aleman</span>",
    unsafe_allow_html=True,
)

pg = st.navigation(
    [
        st.Page("app_pages/pagina_partido.py", title="Analizar Partido",
                icon=":material/sports_soccer:", default=True),
        st.Page("app_pages/pagina_combinada.py", title="Combinada PrimaTips",
                icon=":material/receipt_long:"),
    ],
    position="top",
)
pg.run()

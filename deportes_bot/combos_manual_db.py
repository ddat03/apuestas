# ============================================================
#  combos_manual_db.py — Carrito y combinadas armadas a mano
#  desde dashboard_partido.py
#
#  Creado por Diego Aleman.
#
#  Distinto de clv_db.py: eso trackea los picks AUTOMÁTICOS de
#  sharp_live.py. Esto es el carrito manual de Diego — elige patas
#  de distintos partidos mientras navega el dashboard, se van
#  guardando en SQLite (no en memoria de la sesión) para no
#  perderlas si cierra el navegador, y cuando arma la combinada que
#  quiere, la guarda como un bloque aparte con su cuota total. Cada
#  combinada guardada se puede marcar después como usada/descartada
#  — así, con el tiempo, se puede ver cuántas de las que se armaron
#  realmente se jugaron.
# ============================================================

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).parent / "data" / "combos_manual.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS carrito (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    partido     TEXT NOT NULL,
    liga        TEXT,
    casa        TEXT NOT NULL,
    mercado     TEXT NOT NULL,
    seleccion   TEXT NOT NULL,
    cuota       REAL NOT NULL,
    agregado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS combos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    creado_en   TEXT NOT NULL,
    cuota_total REAL NOT NULL,
    estado      TEXT NOT NULL DEFAULT 'pendiente'   -- pendiente | usada | descartada
);

CREATE TABLE IF NOT EXISTS combo_picks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_id          INTEGER NOT NULL REFERENCES combos(id),
    partido           TEXT NOT NULL,
    liga              TEXT,
    casa              TEXT NOT NULL,
    mercado           TEXT NOT NULL,
    seleccion         TEXT NOT NULL,
    cuota             REAL NOT NULL,
    fecha             TEXT,
    resultado         TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente | acierto | fallo | anulado
    detalle_resultado TEXT
);
"""

# columnas añadidas después del diseño original (bases viejas no las tienen)
_MIGRACIONES = {
    "combo_picks": {
        "fecha": "TEXT",
        "resultado": "TEXT NOT NULL DEFAULT 'pendiente'",
        "detalle_resultado": "TEXT",
    },
}


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    for tabla, cols in _MIGRACIONES.items():
        existentes = {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}
        for col, tipo in cols.items():
            if col not in existentes:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
    return con


def _clave(pick: dict) -> tuple:
    """Identidad de una pata — ignora la cuota (puede moverse entre
    visitas) para no duplicar la misma selección dos veces."""
    return (pick["partido"], pick["casa"], pick["mercado"], pick["seleccion"])


# ── Carrito (en construcción) ─────────────────────────────

def esta_en_carrito(pick: dict) -> bool:
    with _conn() as con:
        fila = con.execute(
            "SELECT 1 FROM carrito WHERE partido=? AND casa=? AND mercado=? AND seleccion=?",
            _clave(pick),
        ).fetchone()
        return fila is not None


def agregar_a_carrito(pick: dict) -> None:
    """pick: {partido, liga, casa, mercado, seleccion, cuota}. No hace
    nada si esa selección puntual ya estaba (evita duplicados)."""
    if esta_en_carrito(pick):
        return
    with _conn() as con:
        con.execute(
            "INSERT INTO carrito (partido, liga, casa, mercado, seleccion, cuota, agregado_en) "
            "VALUES (?,?,?,?,?,?,?)",
            (pick["partido"], pick.get("liga", ""), pick["casa"], pick["mercado"],
             pick["seleccion"], pick["cuota"], datetime.now(timezone.utc).isoformat()),
        )


def quitar_de_carrito(pick: dict) -> None:
    with _conn() as con:
        con.execute(
            "DELETE FROM carrito WHERE partido=? AND casa=? AND mercado=? AND seleccion=?",
            _clave(pick),
        )


def quitar_de_carrito_por_id(pick_id: int) -> None:
    """Borrar una pata puntual del carrito por su id — para el botón
    ❌ de "Mi combinada" (ahí no siempre conviene reconstruir el pick
    completo solo para poder borrarlo)."""
    with _conn() as con:
        con.execute("DELETE FROM carrito WHERE id=?", (pick_id,))


def obtener_carrito() -> list[dict]:
    with _conn() as con:
        filas = con.execute("SELECT * FROM carrito ORDER BY agregado_en").fetchall()
        return [dict(f) for f in filas]


def vaciar_carrito() -> None:
    with _conn() as con:
        con.execute("DELETE FROM carrito")


def cuota_total_carrito() -> float:
    total = 1.0
    for p in obtener_carrito():
        total *= p["cuota"]
    return round(total, 3)


# ── Combinadas guardadas ──────────────────────────────────

def guardar_combinada() -> int | None:
    """Mueve todo lo que hay en el carrito a una combinada nueva.
    None si el carrito estaba vacío (nada que guardar)."""
    patas = obtener_carrito()
    if not patas:
        return None

    cuota_total = cuota_total_carrito()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO combos (creado_en, cuota_total, estado) VALUES (?,?,?)",
            (datetime.now(timezone.utc).isoformat(), cuota_total, "pendiente"),
        )
        combo_id = cur.lastrowid
        for p in patas:
            con.execute(
                "INSERT INTO combo_picks (combo_id, partido, liga, casa, mercado, seleccion, cuota, fecha) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (combo_id, p["partido"], p["liga"], p["casa"], p["mercado"], p["seleccion"],
                 p["cuota"], p.get("fecha")),
            )
        con.execute("DELETE FROM carrito")
    return combo_id


def guardar_combinada_directa(patas: list[dict], cuota_total: float | None = None) -> int | None:
    """Guarda una combinada YA armada (ej. la de PrimaTips) sin pasar
    por el carrito manual. `patas`: [{partido, liga, casa, mercado,
    seleccion, cuota}, ...]. None si la lista está vacía."""
    if not patas:
        return None
    if cuota_total is None:
        cuota_total = 1.0
        for p in patas:
            cuota_total *= p["cuota"]
        cuota_total = round(cuota_total, 3)

    with _conn() as con:
        cur = con.execute(
            "INSERT INTO combos (creado_en, cuota_total, estado) VALUES (?,?,?)",
            (datetime.now(timezone.utc).isoformat(), cuota_total, "pendiente"),
        )
        combo_id = cur.lastrowid
        for p in patas:
            con.execute(
                "INSERT INTO combo_picks (combo_id, partido, liga, casa, mercado, seleccion, cuota, fecha) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (combo_id, p["partido"], p.get("liga", ""), p["casa"], p["mercado"],
                 p["seleccion"], p["cuota"], p.get("fecha")),
            )
    return combo_id


def listar_combinadas() -> list[dict]:
    """Más nuevas primero. Cada una con sus patas anidadas en 'patas'."""
    with _conn() as con:
        combos = [dict(f) for f in con.execute("SELECT * FROM combos ORDER BY id DESC").fetchall()]
        for c in combos:
            c["patas"] = [dict(f) for f in con.execute(
                "SELECT * FROM combo_picks WHERE combo_id=?", (c["id"],)
            ).fetchall()]
        return combos


def marcar_combinada(combo_id: int, estado: str) -> None:
    """estado: 'usada' | 'descartada' | 'pendiente' (¿la jugué?)."""
    assert estado in ("usada", "descartada", "pendiente")
    with _conn() as con:
        con.execute("UPDATE combos SET estado=? WHERE id=?", (estado, combo_id))


def marcar_pata_resultado(pick_id: int, resultado: str, detalle: str = "") -> None:
    """resultado: 'acierto' | 'fallo' | 'anulado' | 'pendiente' (¿pegó?).
    Distinto de `estado` de la combinada, que es si la jugaste o no."""
    assert resultado in ("acierto", "fallo", "anulado", "pendiente")
    with _conn() as con:
        con.execute(
            "UPDATE combo_picks SET resultado=?, detalle_resultado=? WHERE id=?",
            (resultado, detalle, pick_id),
        )


def estado_resultado_combo(combo: dict) -> str:
    """'ganada' (todas acierto/anulado), 'perdida' (alguna falló),
    'pendiente' (falta que terminen partidos)."""
    resultados = [p.get("resultado", "pendiente") for p in combo.get("patas", [])]
    if not resultados:
        return "pendiente"
    if any(r == "fallo" for r in resultados):
        return "perdida"
    if all(r in ("acierto", "anulado") for r in resultados):
        return "ganada"
    return "pendiente"


def eliminar_combinada(combo_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM combo_picks WHERE combo_id=?", (combo_id,))
        con.execute("DELETE FROM combos WHERE id=?", (combo_id,))

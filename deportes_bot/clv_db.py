# ============================================================
#  clv_db.py — Registro de picks y medición de CLV
#
#  CLV (Closing Line Value) = ¿la cuota que tomamos le ganó a
#  la línea de cierre de Pinnacle? Es el ÚNICO indicador
#  adelantado fiable de si un sistema de apuestas tiene edge.
#  Si el CLV medio en decenas de picks es positivo → hay algo
#  real. Si es ~0 o negativo → es ruido, sin importar cuántas
#  apuestas sueltas ganen.
#
#  Todo en SQLite (data/clv.db). 100% papel: ninguna apuesta
#  real se coloca desde aquí.
# ============================================================

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).parent / "data" / "clv.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_pick           TEXT NOT NULL,
    sport             TEXT,
    event_id          TEXT,
    home              TEXT,
    away              TEXT,
    commence_time     TEXT,
    outcome           TEXT,              -- local | empate | visitante
    odds_taken        REAL,
    book_taken        TEXT,
    fair_prob_pick    REAL,              -- prob. justa Pinnacle al momento del pick
    ancla_odds_pick   REAL,
    ev_pick           REAL,
    stake_u           REAL,              -- unidades apostadas (papel)
    status            TEXT DEFAULT 'PENDING',   -- PENDING | SETTLED
    -- se rellenan al cerrar / liquidar:
    ancla_odds_close  REAL,
    fair_prob_close   REAL,
    clv_ev            REAL,              -- ev_vs_cierre: fair_close * odds_taken - 1
    beat_close        INTEGER,           -- 1 si odds_taken > ancla_odds_close
    result            TEXT,              -- WIN | LOSS | PUSH | UNKNOWN
    pnl_u             REAL,
    UNIQUE(event_id, outcome)
);

-- Combinadas "seguras" (combo_builder.py): varias patas de alta
-- probabilidad (no-vig Pinnacle), de partidos/ligas distintas.
-- Cada pata es también una fila de `picks` (reutilizada vía
-- combo_legs) — así se liquida una sola vez y de un solo lado.
CREATE TABLE IF NOT EXISTS combos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_creado         TEXT NOT NULL,
    tipo              TEXT,              -- "2 patas" | "3 patas"
    n_patas           INTEGER,
    cuota_total       REAL,
    prob_producto     REAL,              -- multiplicación simple de fair_prob
    prob_ajustada     REAL,              -- con descuento conservador por correlación
    stake_u           REAL,
    status            TEXT DEFAULT 'PENDING',   -- PENDING | SETTLED
    result            TEXT,              -- WIN | LOSS | PUSH
    pnl_u             REAL
);

CREATE TABLE IF NOT EXISTS combo_legs (
    combo_id  INTEGER NOT NULL REFERENCES combos(id),
    pick_id   INTEGER NOT NULL REFERENCES picks(id),
    PRIMARY KEY (combo_id, pick_id)
);
"""


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    _migrar(c)
    return c


def _migrar(c: sqlite3.Connection) -> None:
    """Migraciones aditivas sobre bases ya creadas con el schema viejo."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(picks)")}
    if "origen" not in cols:
        c.execute("ALTER TABLE picks ADD COLUMN origen TEXT DEFAULT 'sharp_ev'")


def registrar_pick(pick, stake_u: float = 1.0, origen: str = "sharp_ev") -> bool:
    """Inserta un pick nuevo. Devuelve False si ya existía (mismo
    event_id + outcome) — no se re-registra ni se mueve el precio.

    origen: 'sharp_ev' (pick +EV individual) o 'combo_seguro' (pata
    de una combinada, ver combo_builder.py) — solo informativo."""
    with _conn() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO picks
               (ts_pick, sport, event_id, home, away, commence_time, outcome,
                odds_taken, book_taken, fair_prob_pick, ancla_odds_pick, ev_pick,
                stake_u, origen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"),
             pick.sport, pick.event_id, pick.home, pick.away, pick.commence_time,
             pick.outcome, pick.best_odds, pick.best_book, pick.fair_prob,
             pick.ancla_odds, pick.ev, stake_u, origen),
        )
        return cur.rowcount > 0


def pick_id(event_id: str, outcome: str) -> int | None:
    with _conn() as c:
        row = c.execute("SELECT id FROM picks WHERE event_id=? AND outcome=?",
                        (event_id, outcome)).fetchone()
        return row["id"] if row else None


def combo_existe(pick_ids: list[int]) -> bool:
    """True si ya existe una combinada con exactamente este mismo
    conjunto de patas (no importa el orden) — evita duplicar la
    misma combinada en cada ciclo de sharp_live."""
    ids = frozenset(pick_ids)
    with _conn() as c:
        for combo in c.execute("SELECT id FROM combos").fetchall():
            legs = c.execute("SELECT pick_id FROM combo_legs WHERE combo_id=?",
                             (combo["id"],)).fetchall()
            if frozenset(r["pick_id"] for r in legs) == ids:
                return True
    return False


def crear_combo(tipo: str, pick_ids: list[int], cuota_total: float,
                prob_producto: float, prob_ajustada: float,
                stake_u: float = 1.0) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO combos (ts_creado, tipo, n_patas, cuota_total,
                                    prob_producto, prob_ajustada, stake_u)
               VALUES (?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), tipo,
             len(pick_ids), cuota_total, prob_producto, prob_ajustada, stake_u),
        )
        combo_id = cur.lastrowid
        c.executemany(
            "INSERT OR IGNORE INTO combo_legs (combo_id, pick_id) VALUES (?,?)",
            [(combo_id, pid) for pid in pick_ids],
        )
        return combo_id


def liquidar_combos() -> int:
    """Revisa combinadas PENDING: si todas sus patas ya están
    liquidadas (picks.result no nulo), calcula el resultado de la
    combinada. Devuelve cuántas se liquidaron en esta pasada."""
    with _conn() as c:
        combos = c.execute("SELECT * FROM combos WHERE status='PENDING'").fetchall()
        liquidadas = 0
        for combo in combos:
            patas = c.execute(
                """SELECT p.result FROM combo_legs cl
                   JOIN picks p ON p.id = cl.pick_id
                   WHERE cl.combo_id=?""", (combo["id"],)).fetchall()
            resultados = [p["result"] for p in patas]
            if not resultados or any(r is None or r == "UNKNOWN" for r in resultados):
                continue  # todavía falta liquidar alguna pata

            if any(r == "LOSS" for r in resultados):
                result, pnl = "LOSS", -combo["stake_u"]
            elif all(r == "WIN" for r in resultados):
                result, pnl = "WIN", combo["stake_u"] * (combo["cuota_total"] - 1.0)
            else:
                result, pnl = "PUSH", 0.0

            c.execute(
                "UPDATE combos SET status='SETTLED', result=?, pnl_u=? WHERE id=?",
                (result, round(pnl, 3), combo["id"]),
            )
            liquidadas += 1
        return liquidadas


def resumen_combos() -> dict:
    with _conn() as c:
        filas = c.execute("SELECT * FROM combos").fetchall()
    n = len(filas)
    liquidadas = [f for f in filas if f["status"] == "SETTLED" and f["result"] in ("WIN", "LOSS")]
    d = {
        "combos_total": n,
        "pendientes": sum(1 for f in filas if f["status"] == "PENDING"),
        "liquidadas": len(liquidadas),
    }
    if liquidadas:
        pnl   = sum(f["pnl_u"] for f in liquidadas)
        stake = sum(f["stake_u"] for f in liquidadas)
        wins  = sum(1 for f in liquidadas if f["result"] == "WIN")
        d["roi"] = round(pnl / stake, 4) if stake else 0.0
        d["pnl_u"] = round(pnl, 2)
        d["win_rate_real"] = round(wins / len(liquidadas), 3)
        # Calibración: si el sistema es honesto, esto debería acercarse
        # a win_rate_real a medida que se acumulan combinadas liquidadas.
        d["prob_prometida_media"] = round(
            sum(f["prob_ajustada"] for f in liquidadas) / len(liquidadas), 3)
    return d


def pendientes() -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute("SELECT * FROM picks WHERE status='PENDING'").fetchall()


def actualizar_cierre(event_id: str, outcome: str, ancla_odds_close: float,
                      fair_prob_close: float, clv_ev: float, odds_taken: float) -> None:
    """Guarda la línea de cierre en un pick pendiente (sin liquidarlo aún —
    el resultado del partido llega después)."""
    with _conn() as c:
        c.execute(
            """UPDATE picks SET ancla_odds_close=?, fair_prob_close=?, clv_ev=?,
                                beat_close=?
               WHERE event_id=? AND outcome=? AND status='PENDING'""",
            (round(ancla_odds_close, 3), round(fair_prob_close, 4), round(clv_ev, 4),
             1 if odds_taken > ancla_odds_close else 0, event_id, outcome),
        )


def liquidar(event_id: str, outcome: str, result: str) -> None:
    """Marca el pick como cerrado con su resultado y P&L en unidades."""
    with _conn() as c:
        row = c.execute(
            "SELECT odds_taken, stake_u FROM picks WHERE event_id=? AND outcome=?",
            (event_id, outcome)).fetchone()
        if not row:
            return
        if result == "WIN":
            pnl = row["stake_u"] * (row["odds_taken"] - 1.0)
        elif result == "LOSS":
            pnl = -row["stake_u"]
        else:  # PUSH / UNKNOWN
            pnl = 0.0
        c.execute(
            "UPDATE picks SET status='SETTLED', result=?, pnl_u=? WHERE event_id=? AND outcome=?",
            (result, round(pnl, 3), event_id, outcome),
        )


def resumen() -> dict:
    with _conn() as c:
        filas = c.execute("SELECT * FROM picks").fetchall()
    n = len(filas)
    con_cierre = [f for f in filas if f["clv_ev"] is not None]
    liquidados = [f for f in filas if f["status"] == "SETTLED" and f["result"] in ("WIN", "LOSS")]
    d = {
        "picks_total": n,
        "pendientes": sum(1 for f in filas if f["status"] == "PENDING"),
        "con_linea_cierre": len(con_cierre),
        "liquidados": len(liquidados),
    }
    if con_cierre:
        clvs = [f["clv_ev"] for f in con_cierre]
        d["clv_ev_medio"] = round(sum(clvs) / len(clvs), 4)
        d["pct_batio_cierre"] = round(
            sum(f["beat_close"] for f in con_cierre) / len(con_cierre), 3)
    if liquidados:
        pnl = sum(f["pnl_u"] for f in liquidados)
        stake = sum(f["stake_u"] for f in liquidados)
        d["roi"] = round(pnl / stake, 4) if stake else 0.0
        d["pnl_u"] = round(pnl, 2)
        d["win_rate"] = round(sum(1 for f in liquidados if f["result"] == "WIN") / len(liquidados), 3)
    return d

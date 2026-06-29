# ============================================================
#  config.py — Configuración central del sistema
# ============================================================

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────
#  API KEYS
# ─────────────────────────────────────────
API_FOOTBALL_KEY   = os.getenv("API_FOOTBALL_KEY", "")
RAPIDAPI_KEY       = os.getenv("RAPIDAPI_KEY", "")
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
THE_ODDS_API_KEY   = os.getenv("THE_ODDS_API_KEY", "")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL       = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ─────────────────────────────────────────
#  MODO DE EJECUCIÓN
# ─────────────────────────────────────────
MOCK_MODE = not bool(API_FOOTBALL_KEY)

# ─────────────────────────────────────────
#  LIGAS ACTIVAS (IDs de API-Football)
#  Ajusta según la temporada del año:
#    Jun-Jul → Mundial / Copa América
#    Ago-May → Ligas europeas
# ─────────────────────────────────────────
LIGAS = {
    # ─── ACTIVAS AHORA (verano 2026) ─────
    1:   "FIFA World Cup",
    # ─── Ligas europeas (activas ago-may) ─
    39:  "Premier League",
    140: "La Liga",
    135: "Serie A",
    78:  "Bundesliga",
    61:  "Ligue 1",
    2:   "Champions League",
}

# Solo ligas que tienen partidos AHORA
# Cambia a [39,140,135,78,61,2] en agosto cuando empiece la temporada europea
LIGAS_PERMITIDAS = [1]

# Temporada por liga (el Mundial 2026 usa season=2026;
# las ligas europeas 25-26 usan season=2025)
LIGAS_TEMPORADA = {
    1:   "2026",   # World Cup 2026
    39:  "2025",   # Premier League 2025-26
    140: "2025",
    135: "2025",
    78:  "2025",
    61:  "2025",
    2:   "2025",
}
TEMPORADA_ACTUAL = "2026"   # default

# ─────────────────────────────────────────
#  MAPEO PARA THE ODDS API
# ─────────────────────────────────────────
ODDS_SPORT_MAP = {
    1:   "soccer_fifa_world_cup",
    39:  "soccer_epl",
    140: "soccer_spain_la_liga",
    135: "soccer_italy_serie_a",
    78:  "soccer_germany_bundesliga",
    61:  "soccer_france_ligue_one",
    2:   "soccer_uefa_champs_league",
}

# ─────────────────────────────────────────
#  PARÁMETROS DEL MODELO
# ─────────────────────────────────────────
JORNADAS_HISTORICO    = 5
CONFIANZA_MINIMA      = 0.55    # Mundial: bajar un poco (menos datos históricos)
SCORE_MINIMO          = 8
APUESTAS_MAXIMAS_DIA  = 3       # máximo 3 por día con $100 bankroll
TEMPORADAS_HISTORICO  = ["2022", "2023", "2024"]

# ─────────────────────────────────────────
#  GESTIÓN DE RIESGO
# ─────────────────────────────────────────
MONEDA               = "USD"
BANKROLL_INICIAL     = 100.0
APUESTA_BASE         = 5.0
KELLY_FRACTION       = 0.25
LOSS_LIMIT_DIARIO    = 20.0
LOSS_LIMIT_SEMANAL   = 35.0
MAX_APUESTA_PCT      = 0.05    # máximo $5 por apuesta

# ─────────────────────────────────────────
#  RUTAS DE ARCHIVOS
# ─────────────────────────────────────────
import pathlib
BASE_DIR      = pathlib.Path(__file__).parent
DATA_DIR      = BASE_DIR / "data"
MODELS_DIR    = BASE_DIR / "models"
REPORTS_DIR   = BASE_DIR / "reports"
LOGS_DIR      = BASE_DIR / "logs"

CSV_PARTIDOS        = DATA_DIR  / "partidos_proximos.csv"
CSV_RECOMENDACIONES = DATA_DIR  / "recomendaciones_hoy.csv"
DB_APUESTAS         = DATA_DIR  / "historico_apuestas.db"
CSV_HISTORICO       = DATA_DIR  / "historico_partidos.csv"

MODEL_PATH   = MODELS_DIR / "deportes_model.pkl"
SCALER_PATH  = MODELS_DIR / "deportes_scaler.pkl"
REPORT_PATH  = REPORTS_DIR / "model_performance.txt"
STATS_PATH   = REPORTS_DIR / "estadisticas_mensuales.json"

# ─────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────
LOG_FILE  = LOGS_DIR / "sistema.log"
LOG_LEVEL = "INFO"

# ─────────────────────────────────────────
#  APIS
# ─────────────────────────────────────────
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
RAPIDAPI_HOST     = "api-football-v1.p.rapidapi.com"
THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"

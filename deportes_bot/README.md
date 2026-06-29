# Sistema de Análisis Deportivo para Apuestas

Sistema de ML personal para identificar apuestas con valor estadístico positivo.
**No apuesta automáticamente** — solo genera recomendaciones que tú decides seguir o no.

---

## Arquitectura

```
main.py           ← Coordinador (punto de entrada)
config.py         ← Configuración centralizada
data_collector.py ← Obtiene partidos y estadísticas (API-Football + The Odds API)
ml_model.py       ← Entrena RandomForest/GradientBoosting y predice resultados
value_analyzer.py ← Detecta valor (mi prob vs prob del mercado)
bet_tracker.py    ← Registra apuestas, calcula ROI, genera gráficos
telegram_bot.py   ← Envía alertas diarias y recibe resultados
```

---

## Instalación

```bash
# 1. Clonar o copiar el proyecto
cd deportes_bot

# 2. Crear entorno virtual (recomendado)
python -m venv .venv
.venv\Scripts\activate   # Windows
source .venv/bin/activate # Mac/Linux

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar claves de API
cp .env.example .env
# Edita .env con tus claves reales
```

---

## APIs necesarias

| API | Uso | Precio | Link |
|-----|-----|--------|------|
| **API-Football** (via RapidAPI) | Partidos, stats, H2H | Free: 100 req/día | [rapidapi.com](https://rapidapi.com/api-sports/api/api-football) |
| **The Odds API** | Cuotas en tiempo real | Free: 500 req/mes | [the-odds-api.com](https://the-odds-api.com/) |
| **Telegram Bot** | Alertas diarias | Gratis | [@BotFather](https://t.me/BotFather) |

> El sistema funciona **sin API keys** en modo demo/mock con datos ficticios.

---

## Uso

### Modo demo (sin APIs, para probar)
```bash
python main.py --mode demo
```

### Entrenar el modelo (primera vez o mensualmente)
```bash
python main.py --mode train
```

### Predicciones diarias
```bash
python main.py --mode predict
python main.py --mode predict --no-telegram   # sin enviar Telegram
```

### Backtest histórico
```bash
python main.py --mode backtest --start 2024-01-01 --end 2024-06-30
```

### Dashboard de resultados
```bash
python main.py --mode tracker
python main.py --mode tracker --graficos   # genera HTML interactivos
```

### Registrar una apuesta manualmente
```python
from bet_tracker import registrar_apuesta, actualizar_resultado

# Registrar
id_apuesta = registrar_apuesta(
    partido="Manchester City vs Arsenal",
    liga="Premier League",
    prediccion="Local",
    confianza=0.68,
    cuota=1.75,
    monto=25.0,
    score=42.0,
)

# Actualizar resultado cuando termine el partido
actualizar_resultado(id_apuesta, "WIN")  # WIN / LOSS / PUSH
```

---

## Automatización diaria (Windows Task Scheduler)

```
Programa : python
Argumentos: C:\...\deportes_bot\main.py --mode predict
Inicio en : C:\...\deportes_bot
Hora       : 08:00
```

O con el módulo `schedule`:
```python
import schedule, time, subprocess

schedule.every().day.at("08:00").do(
    lambda: subprocess.run(["python", "main.py", "--mode", "predict"])
)
while True:
    schedule.run_pending()
    time.sleep(60)
```

---

## Comandos del Bot de Telegram

Una vez configurado el bot:

| Comando | Acción |
|---------|--------|
| `/resultado 5 WIN` | Marca apuesta #5 como ganada |
| `/resultado 5 LOSS` | Marca apuesta #5 como perdida |
| `/dashboard` | Muestra estadísticas generales |
| `/apuestas` | Lista apuestas pendientes de resultado |

---

## Estructura de archivos

```
deportes_bot/
├── main.py
├── config.py
├── data_collector.py
├── ml_model.py
├── value_analyzer.py
├── bet_tracker.py
├── telegram_bot.py
├── requirements.txt
├── .env.example
├── .env                      ← (crea este, no subir a git)
│
├── data/
│   ├── partidos_proximos.csv      ← partidos descargados hoy
│   ├── recomendaciones_hoy.csv    ← apuestas recomendadas
│   └── historico_apuestas.db      ← SQLite con tus apuestas
│
├── models/
│   ├── deportes_model.pkl         ← modelo entrenado
│   └── deportes_scaler.pkl        ← scaler de features
│
├── reports/
│   ├── model_performance.txt      ← métricas del modelo
│   ├── estadisticas_mensuales.json
│   └── grafico_*.html             ← gráficos interactivos
│
└── logs/
    └── sistema.log
```

---

## Cómo funciona la detección de valor

```
1. El modelo predice: Local 65% de ganar
2. La cuota del mercado es 1.70 → implica 58.8% (= 1/1.70)
3. EDGE = 65% - 58.8% = +6.2% a tu favor
4. EV = (0.65 × 1.70) - 1 = +0.105 (10.5% de retorno esperado)
5. Score = 6.2 × 100 × 0.65 = 40.3 → APOSTAR
```

Solo recomienda si:
- Confianza del modelo ≥ 60%
- Score ≥ 10
- EV > 0
- Edge ≥ 3%

---

## Gestión de riesgo

- **Criterio de Kelly** (fracción 0.25) para calcular el tamaño de apuesta
- Máximo 5% del bankroll por apuesta
- Máximo 5 apuestas por día
- Stop loss diario: $100 | semanal: $300

---

## Roadmap de mejoras

- [ ] Agregar clima como feature
- [ ] Integrar más ligas (MLS, Eredivisie, etc.)
- [ ] Multi-modelo con ensemble
- [ ] Live betting analysis
- [ ] Dashboard web con Streamlit

---

> **Aviso**: Las apuestas deportivas conllevan riesgo de pérdida de capital.
> Este sistema es una herramienta de análisis, no garantiza ganancias.
> Usa siempre dinero que puedes permitirte perder.

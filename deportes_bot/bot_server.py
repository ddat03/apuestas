#!/usr/bin/env python3
# ============================================================
#  bot_server.py — Bot Telegram siempre activo (cloud)
#
#  Comandos disponibles:
#    /analizar   → Corre el análisis completo y envía resultados
#    /resultado <id> WIN|LOSS|PUSH → Registra resultado de apuesta
#    /apuestas   → Lista apuestas pendientes
#    /dashboard  → Resumen de rendimiento
#    /estado     → Estado del sistema (APIs, bankroll, etc.)
#
#  Para correr localmente:
#    python bot_server.py
#
#  Para deploy en Railway:
#    El Procfile ya apunta a este archivo.
# ============================================================

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Asegurar que el directorio actual está en el path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    BANKROLL_INICIAL, MONEDA, LOG_FILE, LOGS_DIR
)

LOGS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("BotServer")


# ══════════════════════════════════════════
#  HANDLERS DE COMANDOS
# ══════════════════════════════════════════

async def cmd_analizar(update, ctx):
    """
    /analizar — Corre el pipeline completo y responde con las mejores apuestas.
    Puede tardar 30-60 segundos porque descarga datos en tiempo real.
    """
    chat_id = update.effective_chat.id

    # Solo responder al chat autorizado
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        await update.message.reply_text("No autorizado.")
        return

    await update.message.reply_text(
        "⏳ Analizando partidos en tiempo real...\n"
        "Descargando cuotas de 25 bookmakers + análisis IA.\n"
        "Espera ~30 segundos."
    )

    try:
        from data_collector    import recolectar_datos
        from value_analyzer    import analizar_todos
        from markets_collector import recolectar_mercados, mercados_a_dict, descargar_todos_mercados
        from ai_analyzer       import analizar_con_ia, formatear_analisis_ia
        from config            import LIGAS_PERMITIDAS

        # 1. Partidos
        df_partidos = recolectar_datos()
        if df_partidos.empty:
            await update.message.reply_text("ℹ️ Sin partidos próximos encontrados.")
            return

        n = len(df_partidos)
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f"✅ {n} partidos encontrados. Descargando mercados..."
        )

        # 2. Mercados adicionales
        cache_por_liga = {
            liga_id: descargar_todos_mercados(liga_id)
            for liga_id in LIGAS_PERMITIDAS
        }
        lista_mercados_dict = []
        for _, row in df_partidos.iterrows():
            liga_id = int(row.get("liga_id", 1))
            pm = recolectar_mercados(row.to_dict(), liga_id,
                                     eventos_cache=cache_por_liga.get(liga_id, []))
            lista_mercados_dict.append(mercados_a_dict(pm))

        # 3. Análisis Sharp
        df_analisis = analizar_todos(df_partidos, bankroll=BANKROLL_INICIAL)

        # 4. Análisis IA
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if openai_key:
            await ctx.bot.send_message(chat_id=chat_id, text="🤖 Generando análisis IA...")
            analisis_ia = analizar_con_ia(
                df_partidos.to_dict("records"),
                lista_mercados_dict,
                bankroll=BANKROLL_INICIAL,
                max_apuesta=BANKROLL_INICIAL * 0.05,
            )
            mensaje = formatear_analisis_ia(analisis_ia)
        else:
            # Fallback: mensaje Sharp sin IA
            from telegram_bot import formatear_mensaje_diario
            mensaje = formatear_mensaje_diario(df_analisis)

        # 5. Enviar resultado
        from telegram import constants
        for i in range(0, len(mensaje), 4000):
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=mensaje[i:i+4000],
                parse_mode=constants.ParseMode.MARKDOWN,
            )

        hora = datetime.now().strftime("%H:%M")
        log.info(f"✓ /analizar completado a las {hora}")

    except Exception as e:
        log.error(f"Error en /analizar: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error al analizar: {e}")


async def cmd_resultado(update, ctx):
    """/resultado <id> WIN|LOSS|PUSH"""
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return

    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: /resultado <id> <WIN|LOSS|PUSH>\n"
            "Ejemplo: /resultado 3 WIN"
        )
        return

    try:
        apuesta_id = int(args[0])
        resultado  = args[1].upper()
        if resultado not in ("WIN", "LOSS", "PUSH"):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("El resultado debe ser WIN, LOSS o PUSH.")
        return

    try:
        from bet_tracker import actualizar_resultado, inicializar_db
        inicializar_db()
        ok  = actualizar_resultado(apuesta_id, resultado)
        msg = f"✅ Apuesta #{apuesta_id} → *{resultado}*" if ok else f"❌ No encontré la apuesta #{apuesta_id}"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_apuestas(update, ctx):
    """/apuestas — Lista apuestas pendientes"""
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return

    try:
        from bet_tracker import inicializar_db, apuestas_table, engine
        import sqlalchemy as sa
        inicializar_db()
        with engine.connect() as conn:
            rows = conn.execute(
                apuestas_table.select()
                .where(apuestas_table.c.resultado == "PENDIENTE")
                .order_by(apuestas_table.c.fecha.desc())
            ).fetchall()

        if not rows:
            await update.message.reply_text("No hay apuestas pendientes ✅")
            return

        msg = "*Apuestas pendientes:*\n"
        for r in rows:
            msg += f"  `#{r.id}` | {r.partido} | *{r.mi_prediccion}* @ {r.cuota} | ${r.monto}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_dashboard(update, ctx):
    """/dashboard — Resumen de rendimiento"""
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return

    try:
        from bet_tracker import estadisticas_generales, inicializar_db
        inicializar_db()
        stats = estadisticas_generales()
        t = stats["total"]

        roi_emoji = "📈" if t["roi_pct"] >= 0 else "📉"
        msg = (
            f"📊 *DASHBOARD — {datetime.now().strftime('%d/%m/%Y')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Apostado total: ${t['apostado']:.2f}\n"
            f"💵 Ganancia/Pérdida: ${t['ganado']:+.2f}\n"
            f"{roi_emoji} ROI: {t['roi_pct']:+.2f}%\n"
            f"🎯 Win rate: {t['winrate']:.1f}%\n"
            f"📋 Total apuestas: {t['n_apuestas']}\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_estado(update, ctx):
    """/estado — Estado de las APIs y sistema"""
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return

    import requests as req

    lineas = [f"🔧 *ESTADO DEL SISTEMA — {datetime.now().strftime('%H:%M')}*", ""]

    # The Odds API
    try:
        r = req.get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": os.getenv("THE_ODDS_API_KEY", "")},
            timeout=5
        )
        rem = r.headers.get("x-requests-remaining", "?")
        lineas.append(f"🎲 The Odds API: ✅  ({rem} requests restantes)")
    except Exception:
        lineas.append("🎲 The Odds API: ❌ Sin conexión")

    # API-Football
    try:
        r = req.get(
            "https://v3.football.api-sports.io/status",
            headers={"x-apisports-key": os.getenv("API_FOOTBALL_KEY", "")},
            timeout=5
        )
        d = r.json()
        sub = d.get("response", {}).get("subscription", {})
        req_day = d.get("response", {}).get("requests", {})
        usado = req_day.get("current", "?")
        limite = req_day.get("limit_day", "?")
        lineas.append(f"⚽ API-Football: ✅  ({usado}/{limite} req hoy)")
    except Exception:
        lineas.append("⚽ API-Football: ❌ Sin conexión")

    # OpenAI
    openai_key = os.getenv("OPENAI_API_KEY", "")
    lineas.append(f"🤖 OpenAI: {'✅ Configurado' if openai_key else '❌ Sin key'}")

    lineas += [
        "",
        f"💰 Bankroll: ${BANKROLL_INICIAL} {MONEDA}",
        f"🕐 Servidor hora: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]

    await update.message.reply_text("\n".join(lineas), parse_mode="Markdown")


async def cmd_ayuda(update, ctx):
    """/ayuda — Lista de comandos"""
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return

    msg = (
        "📋 *COMANDOS DISPONIBLES*\n\n"
        "/analizar — Analiza partidos de hoy en tiempo real\n"
        "/apuestas — Ver apuestas pendientes de resultado\n"
        "/resultado <id> WIN\\|LOSS\\|PUSH — Registrar resultado\n"
        "/dashboard — Ver tu rendimiento total\n"
        "/estado — Ver estado de las APIs\n"
        "/ayuda — Este mensaje\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ══════════════════════════════════════════
#  INICIO DEL BOT
# ══════════════════════════════════════════

def main():
    try:
        from telegram.ext import ApplicationBuilder, CommandHandler
    except ImportError:
        print("ERROR: pip install python-telegram-bot")
        sys.exit(1)

    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN no configurado en .env")
        sys.exit(1)

    log.info("Iniciando bot de Telegram...")
    log.info(f"Chat autorizado: {TELEGRAM_CHAT_ID}")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("analizar",  cmd_analizar))
    app.add_handler(CommandHandler("resultado", cmd_resultado))
    app.add_handler(CommandHandler("apuestas",  cmd_apuestas))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("estado",    cmd_estado))
    app.add_handler(CommandHandler("ayuda",     cmd_ayuda))
    app.add_handler(CommandHandler("start",     cmd_ayuda))

    log.info("Bot listo. Escuchando comandos...")
    log.info("Comandos: /analizar /resultado /apuestas /dashboard /estado /ayuda")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

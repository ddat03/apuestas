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

def _build_keyboard(partidos_lista: list[str], seleccionados: set[int]) -> list:
    """Construye el teclado inline con un botón por partido."""
    from telegram import InlineKeyboardButton
    keyboard = []
    for i, nombre in enumerate(partidos_lista):
        marca = "✅ " if i in seleccionados else ""
        keyboard.append([InlineKeyboardButton(
            text=f"{marca}{nombre}",
            callback_data=f"toggle_{i}",
        )])
    keyboard.append([InlineKeyboardButton(
        text=f"🎯 Analizar ({len(seleccionados)} seleccionados)",
        callback_data="confirmar",
    )])
    return keyboard


async def cmd_analizar(update, ctx):
    """
    /analizar — Muestra los partidos del día como botones.
    El usuario selecciona cuáles analizar y presiona Confirmar.
    """
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        await update.message.reply_text("No autorizado.")
        return

    await update.message.reply_text("⏳ Cargando partidos de hoy…")

    try:
        from data_collector    import recolectar_datos
        from markets_collector import descargar_todos_mercados, recolectar_mercados, mercados_a_dict
        from config            import LIGAS_PERMITIDAS
        from telegram          import InlineKeyboardMarkup

        df = recolectar_datos()
        if df.empty:
            await ctx.bot.send_message(chat_id=chat_id,
                                       text="ℹ️ Sin partidos próximos hoy.")
            return

        # Guardar en user_data para el callback
        partidos_lista = [
            f"{r.get('equipo_local','')} vs {r.get('equipo_visitante','')}"
            for _, r in df.iterrows()
        ]
        cache_liga = {lid: descargar_todos_mercados(lid) for lid in LIGAS_PERMITIDAS}
        mercados_lista = []
        for _, row in df.iterrows():
            lid = int(row.get("liga_id", 1))
            pm  = recolectar_mercados(row.to_dict(), lid,
                                      eventos_cache=cache_liga.get(lid, []))
            mercados_lista.append(mercados_a_dict(pm))

        ctx.user_data["df_partidos"]    = df
        ctx.user_data["partidos_lista"] = partidos_lista
        ctx.user_data["mercados_lista"] = mercados_lista
        ctx.user_data["seleccionados"]  = set(range(len(partidos_lista)))  # todos por defecto

        keyboard = _build_keyboard(partidos_lista, ctx.user_data["seleccionados"])
        await ctx.bot.send_message(
            chat_id=chat_id,
            text="*Selecciona los partidos a analizar:*\n_(toca para marcar/desmarcar)_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        log.error(f"Error en /analizar: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {e}")


async def callback_seleccion(update, ctx):
    """Maneja los botones de selección de partidos."""
    query   = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()

    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return

    data = query.data

    if data.startswith("toggle_"):
        idx = int(data.split("_")[1])
        sel = ctx.user_data.get("seleccionados", set())
        if idx in sel:
            sel.discard(idx)
        else:
            sel.add(idx)
        ctx.user_data["seleccionados"] = sel

        from telegram import InlineKeyboardMarkup
        keyboard = _build_keyboard(ctx.user_data["partidos_lista"], sel)
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "confirmar":
        sel            = ctx.user_data.get("seleccionados", set())
        partidos_lista = ctx.user_data.get("partidos_lista", [])
        df_todos       = ctx.user_data.get("df_partidos")
        mercados_todos = ctx.user_data.get("mercados_lista", [])

        if not sel:
            await query.edit_message_text("⚠️ Selecciona al menos un partido.")
            return

        await query.edit_message_text(
            f"⏳ Analizando {len(sel)} partido(s)…\nEspera ~30 segundos."
        )

        try:
            import pandas as pd
            from value_analyzer import analizar_todos
            from ai_analyzer    import analizar_con_ia, formatear_analisis_ia
            from sentiment_analyzer import analizar_sentimiento, resumen_para_prompt
            from config         import LIGAS_PERMITIDAS

            indices = sorted(sel)
            df_sel  = df_todos.iloc[indices].copy()
            merc_sel= [mercados_todos[i] for i in indices]

            # Noticias
            noticias_ctx = {}
            liga = df_sel.iloc[0].get("liga_nombre", "") if not df_sel.empty else ""
            for _, row in df_sel.iterrows():
                key  = f"{row.get('equipo_local','')} vs {row.get('equipo_visitante','')}"
                sent = analizar_sentimiento(row.get("equipo_local",""),
                                            row.get("equipo_visitante",""),
                                            liga=liga, usar_ia=False)
                if sent.disponible:
                    noticias_ctx[key] = resumen_para_prompt(sent)

            # Análisis Sharp
            df_analisis = analizar_todos(df_sel, bankroll=BANKROLL_INICIAL)

            # Análisis IA
            from telegram import constants
            openai_key = os.getenv("OPENAI_API_KEY", "")
            if openai_key:
                analisis_ia = analizar_con_ia(
                    df_sel.to_dict("records"),
                    merc_sel,
                    bankroll=BANKROLL_INICIAL,
                    max_apuesta=BANKROLL_INICIAL * 0.05,
                    noticias_por_partido=noticias_ctx,
                )
                mensaje = formatear_analisis_ia(analisis_ia)
            else:
                from telegram_bot import formatear_mensaje_diario
                mensaje = formatear_mensaje_diario(df_analisis)

            for i in range(0, len(mensaje), 4000):
                await ctx.bot.send_message(
                    chat_id=chat_id,
                    text=mensaje[i:i+4000],
                    parse_mode=constants.ParseMode.MARKDOWN,
                )
            log.info(f"✓ /analizar ({len(sel)} partidos) completado")

        except Exception as e:
            log.error(f"Error en confirmar análisis: {e}", exc_info=True)
            await ctx.bot.send_message(chat_id=chat_id,
                                       text=f"❌ Error al analizar: {e}")


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

    from telegram.ext import CallbackQueryHandler

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("analizar",  cmd_analizar))
    app.add_handler(CommandHandler("resultado", cmd_resultado))
    app.add_handler(CommandHandler("apuestas",  cmd_apuestas))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("estado",    cmd_estado))
    app.add_handler(CommandHandler("ayuda",     cmd_ayuda))
    app.add_handler(CommandHandler("start",     cmd_ayuda))
    app.add_handler(CallbackQueryHandler(callback_seleccion))

    log.info("Bot listo. Escuchando comandos...")
    log.info("Comandos: /analizar /resultado /apuestas /dashboard /estado /ayuda")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

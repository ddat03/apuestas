# ============================================================
#  telegram_bot.py — Notificaciones diarias por Telegram
# ============================================================

import logging
import asyncio
from datetime import datetime

import pandas as pd

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, MONEDA, LOG_FILE

log = logging.getLogger("TelegramBot")


# ──────────────────────────────────────────────────────────
#  FORMATEADORES
# ──────────────────────────────────────────────────────────

def _emoji_rec(rec: str) -> str:
    return {"APOSTAR": "🟢", "CONSIDERAR": "🟡", "ESPERAR": "🔴", "NO": "⛔"}.get(rec, "⚪")


def _emoji_liga(liga: str) -> str:
    mapa = {
        "World Cup": "🏆", "FIFA": "🏆",
        "Premier":   "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
        "La Liga":   "🇪🇸",
        "Serie A":   "🇮🇹",
        "Bundesliga":"🇩🇪",
        "Ligue 1":   "🇫🇷",
        "Champions": "⭐",
    }
    for k, v in mapa.items():
        if k.lower() in liga.lower():
            return v
    return "⚽"


def _nivel_emoji(nivel: str) -> str:
    return {"Alta": "🔥", "Media": "✅", "Baja": "🟡", "Muy baja": "🔶"}.get(nivel, "⚪")


def formatear_apuesta_sharp(rank: int, row: pd.Series) -> str:
    rec    = row.get("recomendacion", "")
    nivel  = row.get("nivel_confianza", "Media")
    ev     = float(row.get("ev", 0))
    n_bk   = int(row.get("n_bookmakers", 0))
    cuota  = float(row.get("cuota_apuesta", 0))
    pred   = row.get("mi_prediccion", "")
    prob   = float(row.get("confianza", 0))
    apuesta= float(row.get("apuesta_sugerida", 0))

    # Distribución de probabilidades consenso
    pl = float(row.get("local_prob", 0))
    pe = float(row.get("draw_prob",  0))
    pv = float(row.get("away_prob",  0))

    lineas = [
        f"{_emoji_rec(rec)} *#{rank} — {rec}* {_nivel_emoji(nivel)} Confianza {nivel}",
        f"{_emoji_liga(row.get('liga',''))} {row.get('liga','')}",
        f"🆚 *{row.get('partido','')}*",
        f"📊 Consenso mercado ({n_bk} bookmakers):",
        f"   Local {pl:.0%}  |  Empate {pe:.0%}  |  Visit {pv:.0%}",
        f"🎯 Selección: *{pred}*  @  cuota *{cuota}*",
        f"📈 EV: *{ev:+.1%}*  (ganancia esperada por $ apostado)",
    ]
    if apuesta > 0:
        ganancia_pot = round(apuesta * (cuota - 1), 2)
        lineas.append(
            f"💵 Apostar: *{apuesta:.2f} {MONEDA}*  →  ganarías *{ganancia_pot:.2f} {MONEDA}*"
        )
    return "\n".join(lineas)


def formatear_mensaje_diario(df: pd.DataFrame) -> str:
    lineas = [
        f"⚽ *ANÁLISIS SHARP DEL DÍA* — {datetime.now().strftime('%d/%m/%Y')}",
        f"_Probabilidades consenso de bookmakers sin margen_",
        "━" * 32,
    ]

    con_valor = df[df["recomendacion"].isin(["APOSTAR", "CONSIDERAR"])]

    if con_valor.empty:
        lineas += [
            "",
            "ℹ️ Sin apuestas con valor positivo hoy.",
            "El mercado está bien calibrado o no hay partidos.",
        ]
    else:
        # Separar por nivel
        apostar    = con_valor[con_valor["recomendacion"] == "APOSTAR"]
        considerar = con_valor[con_valor["recomendacion"] == "CONSIDERAR"]

        rank = 1
        if not apostar.empty:
            lineas += ["", "🟢 *APOSTAR*", "─" * 28]
            for _, row in apostar.iterrows():
                lineas += ["", formatear_apuesta_sharp(rank, row), "─" * 28]
                rank += 1

        if not considerar.empty:
            lineas += ["", "🟡 *CONSIDERAR*", "─" * 28]
            for _, row in considerar.iterrows():
                lineas += ["", formatear_apuesta_sharp(rank, row), "─" * 28]
                rank += 1

    # Resumen
    n_apostar    = len(df[df["recomendacion"] == "APOSTAR"])
    n_considerar = len(df[df["recomendacion"] == "CONSIDERAR"])
    total_ev     = df[df["ev"] > 0]["ev"].sum()
    lineas += [
        "",
        f"📌 *Resumen:* {len(df)} partidos analizados",
        f"🟢 APOSTAR: {n_apostar}  🟡 CONSIDERAR: {n_considerar}",
        f"📈 EV total acumulado: {total_ev:+.1%}",
        "",
        "_Sistema análisis personal — NO apuesta automáticamente_",
    ]
    return "\n".join(lineas)


# ──────────────────────────────────────────────────────────
#  ENVÍO
# ──────────────────────────────────────────────────────────

async def _enviar_async(mensaje: str) -> bool:
    try:
        from telegram import Bot
        from telegram.constants import ParseMode
    except ImportError:
        log.error("pip install python-telegram-bot")
        return False

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado → mensaje omitido")
        log.info("Mensaje preview:\n" + mensaje)
        return False

    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        for i in range(0, len(mensaje), 4000):
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=mensaje[i:i+4000],
                parse_mode=ParseMode.MARKDOWN,
            )
        log.info(f"✓ Mensaje enviado a chat_id={TELEGRAM_CHAT_ID}")
        return True
    except Exception as e:
        log.error(f"Error Telegram: {e}")
        return False


def enviar_mensaje(mensaje: str) -> bool:
    return asyncio.run(_enviar_async(mensaje))


def enviar_recomendaciones_diarias(df: pd.DataFrame) -> bool:
    mensaje = formatear_mensaje_diario(df)
    log.info("Enviando recomendaciones diarias por Telegram…")
    ok = enviar_mensaje(mensaje)
    if not ok:
        print("\n" + "═"*50 + "\nMENSAJE TELEGRAM (preview):\n" + "═"*50)
        print(mensaje)
        print("═"*50 + "\n")
    return ok


# ──────────────────────────────────────────────────────────
#  BOT INTERACTIVO
# ──────────────────────────────────────────────────────────

async def _bot_interactivo():
    try:
        from telegram import Update
        from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
    except ImportError:
        print("pip install python-telegram-bot")
        return

    from bet_tracker import actualizar_resultado, estadisticas_generales

    async def cmd_resultado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        args = ctx.args
        if len(args) < 2:
            await update.message.reply_text("Uso: /resultado <id> <WIN|LOSS|PUSH>")
            return
        try:
            apuesta_id = int(args[0])
            resultado  = args[1].upper()
            if resultado not in ("WIN","LOSS","PUSH"):
                raise ValueError
        except (ValueError, IndexError):
            await update.message.reply_text("Resultado debe ser WIN, LOSS o PUSH")
            return
        ok  = actualizar_resultado(apuesta_id, resultado)
        msg = f"✅ #{apuesta_id} → {resultado}" if ok else f"❌ No encontré apuesta #{apuesta_id}"
        await update.message.reply_text(msg)

    async def cmd_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        stats = estadisticas_generales()
        t = stats["total"]
        msg = (
            f"📊 *DASHBOARD*\n"
            f"Apostado: {t['apostado']:.2f} {MONEDA}\n"
            f"P&L: {t['ganado']:+.2f} {MONEDA}\n"
            f"ROI: {t['roi_pct']:+.2f}%\n"
            f"Win rate: {t['winrate']:.1f}%\n"
            f"Apuestas: {t['n_apuestas']}\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def cmd_pendientes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from bet_tracker import inicializar_db, apuestas_table, engine
        inicializar_db()
        import sqlalchemy as sa
        with engine.connect() as conn:
            rows = conn.execute(
                apuestas_table.select().where(apuestas_table.c.resultado == "PENDIENTE")
            ).fetchall()
        if not rows:
            await update.message.reply_text("No hay apuestas pendientes ✅")
            return
        msg = "*Apuestas pendientes:*\n" + "".join(
            f"  #{r.id} | {r.partido} | {r.mi_prediccion} @ {r.cuota}\n" for r in rows
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("resultado", cmd_resultado))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("apuestas",  cmd_pendientes))
    log.info("Bot interactivo iniciado. Ctrl+C para detener.")
    await app.run_polling()


def iniciar_bot_interactivo():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN no configurado.")
        return
    asyncio.run(_bot_interactivo())

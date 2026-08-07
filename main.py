import asyncio
import logging
import os
import traceback
import discord
from discord.ext import commands
from database import setup_database
from sznUtils import load_config, fetch_stealth_cookies
from sznLogger import get_logger

logger = get_logger("bot")

# Intents necesarios para el bot
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.voice_states = True

def get_prefix(bot, message):
    if not message.guild:
        return ['td?', '']
    try:
        custom = load_config(f"prefix_{message.guild.id}")
        return custom if custom else 'td?'
    except Exception:
        return 'td?'

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)

@bot.event
async def on_ready():
    logger.info(f'✅ Conectado exitosamente como {bot.user.name} ({bot.user.id})')

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return  # El canal restringido se notifica en check_command_channel
    if isinstance(error, commands.CommandNotFound):
        return
    logger.warning(f"⚠️ Error en comando '{ctx.command}': {error}")

import signal

async def graceful_shutdown(bot):
    if getattr(bot, 'is_draining', False):
        return
    bot.is_draining = True
    logger.warning("🛑 Señal de apagado/reinicio recibida (SIGTERM/SIGINT). Iniciando vaciado suave (Graceful Drain)...")

    # Guardar canciones pendientes antes de reiniciar según la preferencia del servidor
    core = bot.get_cog("MusicCore")
    if core:
        from sznUtils import save_guild_queue, is_guild_persist_enabled
        for player in list(core.players.values()):
            player.radio_mode = False
            persist_enabled = is_guild_persist_enabled(player.guild_id)
            logger.debug(f"🔍 [DRAIN] Guild {player.guild_id} — persist={persist_enabled}, song_queue={len(player.song_queue)} canciones, current_song={'SI' if player.current_song else 'NO'}")
            if persist_enabled:
                full_queue = list(player.song_queue)  # solo pendientes, current_song ya sonará hasta el final
                if full_queue:
                    save_guild_queue(player.guild_id, full_queue)
                    logger.info(f"💾 [DRAIN] Cola guardada para guild {player.guild_id}: {len(full_queue)} canciones pendientes.")
                else:
                    logger.warning(f"⚠️ [DRAIN] Cola vacía para guild {player.guild_id}, nada que guardar.")
            player.song_queue.clear()

    max_wait_seconds = 600  # Límite máximo de seguridad
    waited = 0

    while waited < max_wait_seconds:
        active_voice = [vc for vc in bot.voice_clients if vc.is_connected() and (vc.is_playing() or vc.is_paused())]
        if not active_voice:
            logger.info("✅ Canción finalizada y canal liberado. Procediendo con el apagado inmediato...")
            break
        await asyncio.sleep(2)
        waited += 2

    for vc in list(bot.voice_clients):
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass

    logger.info("👋 Cerrando conexión del bot con Discord...")
    await bot.close()

async def main():
    logger.info("🚀 Inicializando Tooodles Bot...")
    setup_database()

    cookies = None
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 50:
        logger.info("📄 Cargando cookies desde archivo local cookies.txt...")
        try:
            with open("cookies.txt", "r", encoding="utf-8") as f:
                cookies = f.read()
        except Exception as e:
            logger.warning(f"⚠️ Error al leer cookies.txt: {e}")

    if not cookies:
        cookies = load_config("cookies")

    if not cookies:
        logger.info("🔑 Cookies no encontradas en BD ni en cookies.txt. Generando vía Playwright Stealth...")
        cookies = await fetch_stealth_cookies()

    if cookies:
        os.environ["cookies"] = cookies
        logger.info("🔐 Cookies cargadas en variables de entorno.")

    try:
        await bot.load_extension('sznDB')
        logger.info("🧠 Cog 'sznDB' cargado.")

        await bot.load_extension('sznMusic')
        logger.info("🎵 Cog 'sznMusic' cargado.")

        await bot.load_extension('sznUI')
        logger.info("🎛️ Cog 'sznUI' cargado.")

    except Exception as e:
        logger.error(f"❌ Error al cargar cogs: {e.__class__.__name__}: {e}", exc_info=True)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(graceful_shutdown(bot)))
        except (NotImplementedError, AttributeError):
            pass

    token = os.getenv("token_priv")
    if token:
        await bot.start(token)
    else:
        logger.error("❌ Error: Variable de entorno 'token_priv' no encontrada.")

if __name__ == "__main__":
    asyncio.run(main())
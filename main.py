import asyncio
import logging
import os
import traceback
import discord
from discord.ext import commands
from database import setup_database
from sznUtils import load_config, fetch_stealth_cookies

# Configuración básica de logs
logging.basicConfig(level=logging.INFO)

# Intents necesarios para el bot
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.voice_states = True

def get_prefix(bot, message):
    if not message.guild:
        return 'td?'
    try:
        custom = load_config(f"prefix_{message.guild.id}")
        return custom if custom else 'td?'
    except Exception:
        return 'td?'

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f'✅ Conectado exitosamente como {bot.user.name} ({bot.user.id})')

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

import signal

async def graceful_shutdown(bot):
    if getattr(bot, 'is_draining', False):
        return
    bot.is_draining = True
    print("🛑 Señal de apagado/reinicio recibida (SIGTERM/SIGINT). Iniciando vaciado suave (Graceful Drain)...", flush=True)

    # Guardar canciones pendientes antes de reiniciar según la preferencia del servidor
    core = bot.get_cog("MusicCore")
    if core:
        from sznUtils import save_guild_queue, is_guild_persist_enabled
        for player in list(core.players.values()):
            player.radio_mode = False
            persist_enabled = is_guild_persist_enabled(player.guild_id)
            print(f"🔍 [DRAIN] Guild {player.guild_id} — persist={persist_enabled}, song_queue={len(player.song_queue)} canciones, current_song={'SI' if player.current_song else 'NO'}", flush=True)
            if persist_enabled:
                full_queue = list(player.song_queue)  # solo pendientes, current_song ya sonará hasta el final
                if full_queue:
                    save_guild_queue(player.guild_id, full_queue)
                    print(f"💾 [DRAIN] Cola guardada para guild {player.guild_id}: {len(full_queue)} canciones pendientes.", flush=True)
                else:
                    print(f"⚠️ [DRAIN] Cola vacía para guild {player.guild_id}, nada que guardar.", flush=True)
            player.song_queue.clear()

    max_wait_seconds = 600  # Límite máximo de seguridad
    waited = 0

    while waited < max_wait_seconds:
        active_voice = [vc for vc in bot.voice_clients if vc.is_connected() and (vc.is_playing() or vc.is_paused())]
        if not active_voice:
            print("✅ Canción finalizada y canal liberado. Procediendo con el apagado inmediato...", flush=True)
            break
        await asyncio.sleep(2)
        waited += 2

    for vc in list(bot.voice_clients):
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass

    print("👋 Cerrando conexión del bot con Discord...", flush=True)
    await bot.close()

async def main():
    print("🚀 Inicializando Tooodles Bot...")
    setup_database()

    cookies = None
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 50:
        print("📄 Cargando cookies desde archivo local cookies.txt...")
        try:
            with open("cookies.txt", "r", encoding="utf-8") as f:
                cookies = f.read()
        except Exception as e:
            print(f"⚠️ Error al leer cookies.txt: {e}")

    if not cookies:
        cookies = load_config("cookies")

    if not cookies:
        print("🔑 Cookies no encontradas en BD ni en cookies.txt. Generando vía Playwright Stealth...")
        cookies = await fetch_stealth_cookies()

    if cookies:
        os.environ["cookies"] = cookies
        print("🔐 Cookies cargadas en variables de entorno.")

    try:
        await bot.load_extension('sznDB')
        print("🧠 Cog 'sznDB' cargado.")

        await bot.load_extension('sznMusic')
        print("🎵 Cog 'sznMusic' cargado.")

        await bot.load_extension('sznUI')
        print("🎛️ Cog 'sznUI' cargado.")

    except Exception as e:
        print(f"❌ Error al cargar cogs: {e.__class__.__name__}: {e}")
        traceback.print_exc()

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
        print("❌ Error: Variable de entorno 'token_priv' no encontrada.")

if __name__ == "__main__":
    asyncio.run(main())
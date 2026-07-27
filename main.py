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

bot = commands.Bot(command_prefix='td?', intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f'✅ Conectado exitosamente como {bot.user.name} ({bot.user.id})')

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

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

    token = os.getenv("token_priv")
    if token:
        await bot.start(token)
    else:
        print("❌ Error: Variable de entorno 'token_priv' no encontrada.")

if __name__ == "__main__":
    asyncio.run(main())
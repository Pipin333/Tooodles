import asyncio
import os
import random
import tempfile
import discord
from discord.ext import commands, tasks
from rapidfuzz import process, fuzz

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from database import add_or_update_song, get_db_session, Song, UserLike
from sznUtils import extract_info, fetch_stealth_cookies

SPOTIFY_CLIENT_ID = os.getenv('client_id')
SPOTIFY_CLIENT_SECRET = os.getenv('client_secret')

async def prefetch_chunk(song_info: dict) -> str | None:
    """
    Descarga los primeros 2 MB (bytes=0-2097152) del stream de audio a /tmp/cache_{id}.webm
    para garantizar 0ms de latencia inicial al reproducir en FFmpeg.
    """
    song_id = song_info.get('id')
    stream_url = song_info.get('url')
    if not song_id or not stream_url or not stream_url.startswith("http"):
        return None

    cache_path = os.path.join(tempfile.gettempdir(), f"cache_{song_id}.webm")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        return cache_path

    headers = {
        "Range": "bytes=0-2097152",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    try:
        import aiohttp
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(stream_url, timeout=5) as resp:
                if resp.status in (200, 206):
                    content = await resp.read()
                    with open(cache_path, "wb") as f:
                        f.write(content)
                    print(f"⚡ Chunk de 2MB precargado en caché: {cache_path}", flush=True)
                    return cache_path
    except Exception as e:
        print(f"⚠️ Error al precargar chunk para {song_id}: {e}", flush=True)

    return None

def cleanup_cache(song_info: dict | None = None):
    """Limpia los archivos parciales en /tmp."""
    try:
        if song_info and song_info.get('id'):
            cache_path = os.path.join(tempfile.gettempdir(), f"cache_{song_info['id']}.webm")
            if os.path.exists(cache_path):
                os.remove(cache_path)
                print(f"🧹 Cache parcial eliminado: {cache_path}", flush=True)
        else:
            temp_dir = tempfile.gettempdir()
            for fname in os.listdir(temp_dir):
                if fname.startswith("cache_") and fname.endswith(".webm"):
                    try:
                        os.remove(os.path.join(temp_dir, fname))
                    except Exception:
                        pass
    except Exception as e:
        print(f"⚠️ Error durante cleanup de cache: {e}", flush=True)

def fuzzy_find_songs(query: str, song_list: list[dict], limit: int = 10) -> list[dict]:
    """Aplica fuzzy matching con rapidfuzz sobre una lista de canciones."""
    if not song_list:
        return []
    choices = [f"{s.get('title', '')} {s.get('uploader', '')}".strip() for s in song_list]
    results = process.extract(query, choices, scorer=fuzz.token_sort_ratio, limit=limit)
    matched = []
    for match in results:
        score = match[1]
        index = match[2]
        if score > 30:
            matched.append(song_list[index])
    return matched


class MusicCore(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.song_queue = []
        self.current_song = None
        self.voice_client = None
        self.radio_seed_id = None
        self.radio_mode = False
        self.radio_temperature = 0.75
        self.is_loading_song = False

        try:
            if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
                print("🔁 Conectando con Spotify API...")
                self.sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                    client_id=SPOTIFY_CLIENT_ID,
                    client_secret=SPOTIFY_CLIENT_SECRET
                ))
                print("✅ Conexión a Spotify establecida.")
            else:
                print("ℹ️ Credenciales de Spotify no encontradas (client_id / client_secret). Búsquedas limitadas a YouTube.")
                self.sp = None
        except Exception as e:
            print(f"❌ Error al conectar con Spotify: {e}")
            self.sp = None

        try:
            self.inactivity_check.start()
        except Exception as e:
            print(f"❌ Error al iniciar inactivity_check: {e}")

    def format_duration(self, seconds):
        if not seconds:
            return "EN VIVO"
        mins = int(seconds) // 60
        secs = int(seconds) % 60
        return f"{mins}:{secs:02d}"

    async def connect_to_voice(self, ctx):
        if not ctx.author.voice:
            await ctx.send("❌ ¡Debes estar en un canal de voz para usar este comando!")
            return None

        target_channel = ctx.author.voice.channel

        if ctx.guild.voice_client:
            self.voice_client = ctx.guild.voice_client
            if self.voice_client.channel != target_channel:
                await self.voice_client.move_to(target_channel)
            return self.voice_client

        try:
            print(f"🔊 Conectando al canal de voz: {target_channel.name}...")
            self.voice_client = await target_channel.connect(timeout=15.0, reconnect=True)
            return self.voice_client
        except Exception as e:
            print(f"❌ Error de conexión al canal de voz: {e}")
            await ctx.send("❌ Error al conectar al canal de voz.")
            return None

    async def add_song_dict(self, ctx, song_info: dict, origin: str = "🎵 Solicitada"):
        song_info['origin'] = origin
        self.song_queue.append(song_info)

        try:
            add_or_update_song(song_info['title'], song_info.get('id') or song_info['title'], duration=song_info.get('duration', 0))
        except Exception as e:
            print(f"⚠️ No se pudo guardar la canción en BD: {e}")

        await ctx.send(f"🎶 Añadido a la cola: **{song_info['title']}** ({self.format_duration(song_info.get('duration', 0))})")

        is_busy = self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused())
        if not self.current_song and not is_busy:
            await self.play_next(ctx)

    async def add_from_youtube(self, ctx, query, origin="🎵 Búsqueda de YouTube"):
        self.is_loading_song = True
        try:
            info = await extract_info(query)
            await self.add_song_dict(ctx, info, origin)
        except Exception as e:
            print(f"❌ Error interno en la búsqueda/extracción: {e}", flush=True)
            await ctx.send("❌ No se pudo procesar o encontrar la canción solicitada.")
        finally:
            self.is_loading_song = False

    async def add_from_spotify(self, ctx, url):
        if not self.sp:
            await ctx.send("❌ La API de Spotify no está configurada.")
            return
        try:
            track_id = url.split("/")[-1].split("?")[0]
            track = self.sp.track(track_id)
            query = f"{track['name']} {track['artists'][0]['name']}"
            await self.add_from_youtube(ctx, query, origin=f"🎵 Spotify por {ctx.author.name}")
        except Exception as e:
            print(f"❌ Error al procesar enlace de Spotify: {e}", flush=True)
            await ctx.send("❌ Error al procesar el enlace de Spotify.")

    async def add_playlist_from_spotify(self, ctx, url):
        if not self.sp:
            await ctx.send("❌ La API de Spotify no está configurada.")
            return
        try:
            playlist_id = url.split("/")[-1].split("?")[0]
            results = self.sp.playlist_tracks(playlist_id)
            items = results.get('items', [])
            await ctx.send(f"🔄 Cargando playlist de Spotify ({len(items)} canciones)...")
            for item in items:
                track = item.get('track')
                if track:
                    query = f"{track['name']} {track['artists'][0]['name']}"
                    await self.add_from_youtube(ctx, query, origin=f"🎵 Playlist por {ctx.author.name}")
        except Exception as e:
            print(f"❌ Error al procesar playlist de Spotify: {e}", flush=True)
            await ctx.send("❌ Error al cargar la playlist de Spotify.")

    async def play_next(self, ctx):
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            return

        if not self.song_queue:
            if self.radio_mode and self.radio_seed_id:
                await self.expand_radio_queue(ctx)
            else:
                await ctx.send("📭 La cola de canciones está vacía.")
                self.current_song = None
                return

        self.current_song = self.song_queue.pop(0)

        # Notificar a UI
        ui = self.bot.get_cog("MusicUI")
        if ui:
            await ui.notify_now_playing(ctx, self.current_song['title'], self.current_song.get('origin'))

        musicdb = getattr(self.bot, "musicdb", None)
        if musicdb:
            musicdb.log_song(self.current_song['title'])

        target_path = self.current_song.get('url')

        before_opts = '-probesize 32k -analyzeduration 0'
        if target_path and target_path.startswith("http"):
            before_opts += ' -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        ffmpeg_options = '-vn -threads 2'

        def after_playing(error):
            if error:
                print(f"⚠️ Error en reproducción de FFmpeg: {error}", flush=True)
            self.current_song = None
            self.bot.loop.create_task(self.play_next(ctx))

        try:
            audio_source = await discord.FFmpegOpusAudio.from_probe(
                target_path,
                before_options=before_opts,
                options=ffmpeg_options
            )
        except Exception as opus_err:
            print(f"ℹ️ Opus probe fallback to PCMAudio: {opus_err}", flush=True)
            audio_source = discord.FFmpegPCMAudio(
                target_path,
                before_options=before_opts,
                options=ffmpeg_options
            )

        if self.voice_client and self.voice_client.is_connected():
            self.voice_client.play(audio_source, after=after_playing)
        else:
            await ctx.send("⚠️ El bot fue desconectado del canal de voz.")

    async def expand_radio_queue(self, ctx):
        with get_db_session() as session:
            songs = session.query(Song).all()
            if not songs:
                await ctx.send("⚠️ No hay canciones en la base de datos para generar la radio.")
                self.radio_mode = False
                return
            song_dicts = [{'id': s.youtube_id, 'title': s.title, 'duration': s.duration} for s in songs]

        selected = random.choice(song_dicts)
        await ctx.send(f"📻 Radio Automática: **{selected['title']}**")
        await self.add_from_youtube(ctx, selected['title'], origin="📻 Radio Automática")

    # ==============================================================================
    # COMANDOS DE DISCORD
    # ==============================================================================

    @commands.command(name="p", aliases=["play"])
    async def play(self, ctx, *, query: str):
        """Reproduce una canción de YouTube, Spotify o SoundCloud."""
        vc = await self.connect_to_voice(ctx)
        if not vc:
            return

        if "spotify.com/track" in query:
            await self.add_from_spotify(ctx, query)
        elif "spotify.com/playlist" in query:
            await self.add_playlist_from_spotify(ctx, query)
        else:
            await self.add_from_youtube(ctx, query, origin=f"🎵 Pedida por {ctx.author.name}")

    @commands.command(name="s", aliases=["skip"])
    async def skip(self, ctx):
        """Salta la canción actual."""
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
            await ctx.send("⏭️ Canción saltada.")
        else:
            await ctx.send("⚠️ No hay ninguna canción reproduciéndose.")

    @commands.command(name="q", aliases=["queue"])
    async def queue(self, ctx):
        """Muestra la cola de canciones actual."""
        if not self.song_queue:
            await ctx.send("📭 La cola de canciones está vacía.")
            return

        lines = [f"{idx+1}. **{s['title']}** ({self.format_duration(s.get('duration', 0))})" for idx, s in enumerate(self.song_queue[:10])]
        content = "\n".join(lines)
        if len(self.song_queue) > 10:
            content += f"\n... y {len(self.song_queue) - 10} canciones más."

        embed = discord.Embed(title="🎶 Cola de Canciones", description=content, color=discord.Color.blue())
        await ctx.send(embed=embed)

    @commands.command(name="np", aliases=["nowplaying"])
    async def nowplaying(self, ctx):
        """Muestra la canción reproduciéndose actualmente."""
        if not self.current_song:
            await ctx.send("⚠️ No hay ninguna canción en reproducción.")
            return

        embed = discord.Embed(
            title="🎧 Sonando Ahora",
            description=f"**{self.current_song['title']}**",
            color=discord.Color.green()
        )
        embed.add_field(name="Duración", value=self.format_duration(self.current_song.get('duration', 0)))
        embed.add_field(name="Origen", value=self.current_song.get('origin', 'Desconocido'))
        await ctx.send(embed=embed)

    @commands.command()
    async def pause(self, ctx):
        """Pausa la canción actual."""
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            await ctx.send("⏸️ Reproducción pausada.")

    @commands.command()
    async def resume(self, ctx):
        """Reanuda la reproducción pausada."""
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            await ctx.send("▶️ Reproducción reanudada.")

    @commands.command()
    async def stop(self, ctx):
        """Detiene la música y limpia la cola."""
        self.song_queue.clear()
        cleanup_cache()
        if self.voice_client:
            self.voice_client.stop()
            await self.voice_client.disconnect()
            self.voice_client = None
        self.current_song = None
        await ctx.send("🛑 Reproducción detenida y cola limpiada.")

    @commands.command()
    async def shuffle(self, ctx):
        """Mezcla la cola de canciones aleatoriamente."""
        if not self.song_queue:
            await ctx.send("⚠️ La cola está vacía.")
            return
        random.shuffle(self.song_queue)
        await ctx.send("🔀 Cola de canciones mezclada aleatoriamente.")

    @commands.command()
    async def remove(self, ctx, index: int):
        """Elimina una canción específica de la cola según su número."""
        if index < 1 or index > len(self.song_queue):
            await ctx.send(f"❌ Número fuera de rango. La cola tiene {len(self.song_queue)} canciones.")
            return
        removed = self.song_queue.pop(index - 1)
        cleanup_cache(removed)
        await ctx.send(f"🗑️ Canción eliminada de la cola: **{removed['title']}**")

    @commands.command()
    async def move(self, ctx, old_index: int, new_index: int):
        """Mueve una canción de una posición a otra en la cola."""
        if old_index < 1 or old_index > len(self.song_queue) or new_index < 1 or new_index > len(self.song_queue):
            await ctx.send("❌ Índices fuera de rango.")
            return
        song = self.song_queue.pop(old_index - 1)
        self.song_queue.insert(new_index - 1, song)
        await ctx.send(f"↕️ **{song['title']}** movida de la posición {old_index} a la {new_index}.")

    @commands.command()
    async def search(self, ctx, *, query: str):
        """Busca y permite seleccionar canciones usando fuzzy matching o búsqueda de Piped."""
        try:
            info = await extract_info(query)
            await self.add_song_dict(ctx, info, origin=f"🔍 Búsqueda por {ctx.author.name}")
        except Exception as e:
            await ctx.send(f"❌ No se encontraron resultados para: '{query}'")



    @tasks.loop(seconds=120)
    async def inactivity_check(self):
        if getattr(self, 'is_loading_song', False):
            return
        if self.voice_client and not self.voice_client.is_playing() and not getattr(self.voice_client, 'is_paused', lambda: False)() and not self.song_queue:
            await self.voice_client.disconnect()
            self.voice_client = None
            self.current_song = None
            cleanup_cache()
            print("✅ Desconectado por inactividad.", flush=True)

    @commands.command()
    async def radio(self, ctx, *, arg: str = "0.75"):
        """Activa o desactiva el modo radio automática."""
        if arg.lower() == "off":
            self.radio_mode = False
            self.radio_seed_id = None
            await ctx.send("🛑 Modo radio desactivado.")
            return

        try:
            self.radio_temperature = float(arg)
        except ValueError:
            await ctx.send("❌ Parámetro inválido. Usa un número entre 0.0 y 1.0 o 'off'.")
            return

        if not self.current_song:
            await ctx.send("⚠️ No hay ninguna canción reproduciéndose para iniciar el modo radio.")
            return

        self.radio_mode = True
        self.radio_seed_id = self.current_song.get('id', self.current_song['title'])
        await ctx.send(f"📻 Modo radio activado con temperatura {self.radio_temperature}.")

async def setup(bot):
    await bot.add_cog(MusicCore(bot))
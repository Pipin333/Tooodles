import asyncio
import os
import random
import tempfile
import discord
from discord.ext import commands, tasks
from yt_dlp import YoutubeDL
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from database import add_or_update_song
from sznUtils import fetch_stealth_cookies

SPOTIFY_CLIENT_ID = os.getenv('client_id')
SPOTIFY_CLIENT_SECRET = os.getenv('client_secret')

class MusicCore(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.song_queue = []
        self.current_song = None
        self.voice_client = None
        self.radio_seed_id = None
        self.radio_mode = False
        self.radio_temperature = 0.75
        self.cookie_file = None

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

        self.cookie_file = self.setup_cookies()

        try:
            self.inactivity_check.start()
        except Exception as e:
            print(f"❌ Error al iniciar inactivity_check: {e}")

    def setup_cookies(self):
        cookies_content = os.getenv('cookies')
        if not cookies_content:
            print("⚠️ No hay cookies en memoria. Intentando obtener automáticamente vía Stealth...")
            return None

        try:
            temp = tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8', suffix='.txt', newline='\n')
            temp.write(cookies_content)
            temp.close()
            print(f"✅ Cookies cargadas en archivo temporal: {temp.name}")
            return temp.name
        except Exception as e:
            print(f"❌ Error al crear archivo temporal de cookies: {e}")
            return None

    def get_ydl_opts(self):
        return {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "cookiefile": self.cookie_file if self.cookie_file else None,
            "default_search": "ytsearch",
            "extractor_args": {
                "youtube": {
                    "player_client": ["mweb", "ios", "android", "tv"]
                }
            }
        }

    def format_duration(self, duration):
        hours, remainder = divmod(duration or 0, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"
        return f"{int(minutes):02}:{int(seconds):02}"

    async def connect_to_voice(self, ctx):
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("⚠️ Debes estar en un canal de voz para usar este comando.")
            return None

        target_channel = ctx.author.voice.channel

        if ctx.guild.voice_client:
            guild_vc = ctx.guild.voice_client
            if guild_vc.is_connected():
                if guild_vc.channel != target_channel:
                    await guild_vc.move_to(target_channel)
                self.voice_client = guild_vc
                return self.voice_client
            else:
                try:
                    await guild_vc.disconnect(force=True)
                except Exception:
                    pass

        try:
            self.voice_client = await target_channel.connect(timeout=15.0, reconnect=True)
        except Exception as e:
            print(f"❌ Error al conectar al canal de voz: {e}")
            await ctx.send(f"❌ No me pude conectar al canal de voz: {e}")
            return None

        return self.voice_client

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member == self.bot.user and after.channel is None:
            print("⚠️ Bot desconectado del canal de voz. Limpiando estado...")
            self.voice_client = None
            self.current_song = None
            self.song_queue.clear()

    async def add_song(self, ctx, title, url=None, duration=0, origin="🎵 Añadida manualmente"):
        song = {'title': title, 'url': url, 'duration': duration, 'origin': origin}
        self.song_queue.append(song)
        try:
            add_or_update_song(title, url or ('ytsearch:' + title), duration=duration)
        except Exception as e:
            print(f"⚠️ No se pudo guardar la canción en BD: {e}")
            
        await ctx.send(f"🎶 Añadido a la cola: **{title}** ({self.format_duration(duration)})")
        if not self.current_song:
            await self.play_next(ctx)

    async def search_youtube(self, query):
        ydl_opts = self.get_ydl_opts()
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                return info['entries'][0] if 'entries' in info else info
        except Exception as e:
            err_str = str(e).lower()
            if "sign in" in err_str or "bot" in err_str:
                print("🕵️ Detección de bot en YouTube. Intentando refrescar cookies vía Playwright Stealth...")
                new_cookies = await fetch_stealth_cookies()
                if new_cookies:
                    os.environ["cookies"] = new_cookies
                    self.cookie_file = self.setup_cookies()
                    ydl_opts = self.get_ydl_opts()
                    with YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(query, download=False)
                        return info['entries'][0] if 'entries' in info else info
            raise e

    async def add_from_youtube(self, ctx, query, origin="🎵 Búsqueda de YouTube"):
        musicdb = getattr(self.bot, "musicdb", None)
        match = musicdb.find_similar_song(query) if musicdb else None
        
        if match:
            await self.add_song(ctx, match.title, match.url, match.duration, origin)
            return

        try:
            info = await self.search_youtube(query)
            await self.add_song(ctx, info['title'], info['url'], info.get('duration', 0), origin)
        except Exception as e:
            await ctx.send(f"❌ Error al buscar canción en YouTube: {e}")

    async def add_from_spotify(self, ctx, url):
        if not self.sp:
            await ctx.send("❌ La API de Spotify no está disponible.")
            return
        try:
            track_id = url.split("/")[-1].split("?")[0]
            track = self.sp.track(track_id)
            query = f"{track['name']} {track['artists'][0]['name']}"
            await self.add_from_youtube(ctx, query, origin=f"🎵 Desde Spotify por {ctx.author.name}")
        except Exception as e:
            await ctx.send(f"❌ Error al procesar enlace de Spotify: {e}")

    async def add_playlist_from_spotify(self, ctx, url):
        if not self.sp:
            await ctx.send("❌ La API de Spotify no está disponible.")
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
            await ctx.send(f"❌ Error al cargar playlist de Spotify: {e}")

    async def play_next(self, ctx):
        if not self.song_queue:
            if self.radio_mode and self.radio_seed_id:
                await self.expand_radio_queue(ctx)
            else:
                await ctx.send("📭 La cola de canciones está vacía.")
                self.current_song = None
                return

        self.current_song = self.song_queue.pop(0)
        ui = self.bot.get_cog("MusicUI")
        if ui:
            await ui.notify_now_playing(ctx, self.current_song['title'], self.current_song.get('origin'))

        musicdb = getattr(self.bot, "musicdb", None)
        if musicdb:
            musicdb.log_song(self.current_song['title'])

        def after_playing(error):
            if error:
                print(f"⚠️ Error en reproducción de FFmpeg: {error}")
            self.bot.loop.create_task(self.play_next(ctx))

        ffmpeg_before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        ffmpeg_options = '-vn'

        if self.voice_client and self.voice_client.is_connected():
            self.voice_client.play(
                discord.FFmpegPCMAudio(
                    self.current_song['url'],
                    before_options=ffmpeg_before_options,
                    options=ffmpeg_options
                ),
                after=after_playing
            )
        else:
            await ctx.send("⚠️ El bot fue desconectado del canal de voz.")

    @commands.command(name="p", aliases=["play"])
    async def play(self, ctx, *, query: str):
        """Reproduce una canción o playlist desde YouTube o Spotify."""
        vc = await self.connect_to_voice(ctx)
        if not vc:
            return

        if "spotify.com/track" in query:
            await self.add_from_spotify(ctx, query)
        elif "spotify.com/playlist" in query:
            await self.add_playlist_from_spotify(ctx, query)
        else:
            await self.add_from_youtube(ctx, query, origin=f"🎵 Pedida por {ctx.author.name}")

    @commands.command()
    async def skip(self, ctx):
        """Salta la canción actual."""
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
            await ctx.send("⏭️ Canción saltada.")
        else:
            await ctx.send("⚠️ No hay ninguna canción reproduciéndose.")

    @commands.command()
    async def pause(self, ctx):
        """Pausa la canción actual."""
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            await ctx.send("⏸️ Canción pausada.")

    @commands.command()
    async def resume(self, ctx):
        """Reanuda la canción pausada."""
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            await ctx.send("▶️ Canción reanudada.")

    @commands.command()
    async def clear(self, ctx):
        """Limpia toda la cola de canciones."""
        self.song_queue.clear()
        await ctx.send("🧹 Cola de canciones limpiada.")

    @commands.command()
    async def shuffle(self, ctx):
        """Mezcla aleatoriamente las canciones de la cola."""
        if not self.song_queue:
            await ctx.send("📭 La cola está vacía, no hay nada que mezclar.")
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
        """Busca las 10 mejores coincidencias en YouTube para que elijas cuál reproducir."""
        ydl_opts = self.get_ydl_opts()
        try:
            await ctx.send(f"🔍 Buscando **{query}** en YouTube...")
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch10:{query}", download=False)
                entries = info.get('entries', [])
                if not entries:
                    await ctx.send("❌ No se encontraron resultados.")
                    return

                msg = "**🎵 Resultados encontrados:**\n"
                for i, entry in enumerate(entries[:10], 1):
                    duration = self.format_duration(entry.get('duration', 0))
                    msg += f"{i}. **{entry.get('title')}** ({duration})\n"
                msg += "\n*Responde con el número de la canción que deseas reproducir (o espera 20s para cancelar).* "

                await ctx.send(msg)

                def check(m):
                    return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()

                try:
                    response = await self.bot.wait_for('message', timeout=20.0, check=check)
                    choice = int(response.content) - 1
                    if 0 <= choice < len(entries):
                        selected = entries[choice]
                        vc = await self.connect_to_voice(ctx)
                        if vc:
                            await self.add_song(ctx, selected['title'], selected['url'], selected.get('duration', 0), f"🔎 Selección por {ctx.author.name}")
                    else:
                        await ctx.send("❌ Número de opción inválido.")
                except asyncio.TimeoutError:
                    await ctx.send("⏳ Tiempo agotado para seleccionar.")
        except Exception as e:
            await ctx.send(f"❌ Error durante la búsqueda: {e}")

    @commands.command(name="np", aliases=["nowplaying"])
    async def now_playing(self, ctx):
        """Muestra la canción que se está reproduciendo actualmente."""
        if self.current_song:
            duration_str = self.format_duration(self.current_song.get('duration', 0))
            await ctx.send(f"🎵 Reproduciendo: **{self.current_song['title']}** ({duration_str})")
        else:
            await ctx.send("📭 No hay ninguna canción en reproducción.")

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx):
        """Muestra la cola actual de canciones."""
        if not self.song_queue:
            await ctx.send("🎵 La cola está vacía.")
            return

        msg = "**🎵 Cola de reproducciones:**\n"
        for i, song in enumerate(self.song_queue[:15], 1):
            duration = self.format_duration(song.get('duration', 0))
            msg += f"{i}. **{song['title']}** ({duration})\n"
        
        if len(self.song_queue) > 15:
            msg += f"\n*... y {len(self.song_queue) - 15} canciones más. Usa `td?queueui` para ver la cola interactiva.*"

        await ctx.send(msg)

    @commands.command(aliases=["leave"])
    async def stop(self, ctx):
        """Detiene la reproducción y desconecta al bot del canal de voz."""
        if self.voice_client:
            await self.voice_client.disconnect()
            self.voice_client = None
            self.song_queue.clear()
            self.current_song = None
            await ctx.send("🛑 Reproducción detenida y desconectado del canal de voz.")
        else:
            await ctx.send("⚠️ No estoy en un canal de voz.")

    @commands.command()
    async def help(self, ctx):
        """Muestra la lista de comandos disponibles en Tooodles."""
        embed = discord.Embed(
            title="🤖 Menú de Ayuda de Tooodles Bot",
            description="Aquí tienes la lista completa de comandos disponible:",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="🎵 Música",
            value="`td?p <búsqueda/URL>` - Reproduce música de YouTube o Spotify.\n"
                  "`td?search <búsqueda>` - Busca y te permite elegir entre 10 opciones.\n"
                  "`td?skip` - Salta la canción actual.\n"
                  "`td?pause` - Pausa la reproducción.\n"
                  "`td?resume` - Reanuda la reproducción.\n"
                  "`td?stop` / `td?leave` - Detiene y desconecta el bot.\n"
                  "`td?np` - Muestra la canción actual.",
            inline=False
        )
        embed.add_field(
            name="📜 Gestión de Cola",
            value="`td?queue` / `td?q` - Muestra la cola actual.\n"
                  "`td?queueui` - Muestra la cola con interfaz gráfica de botones.\n"
                  "`td?shuffle` - Mezcla la cola aleatoriamente.\n"
                  "`td?remove <n>` - Elimina la canción #n de la cola.\n"
                  "`td?move <origen> <destino>` - Reordena una canción en la cola.\n"
                  "`td?clear` - Vacía la cola completa.",
            inline=False
        )
        embed.add_field(
            name="❤️ Favoritos y Radio",
            value="`td?like` - Agrega la canción actual a tus favoritas.\n"
                  "`td?unlike` - Quita la canción de tus favoritas.\n"
                  "`td?liked` - Muestra tus canciones favoritas.\n"
                  "`td?favradio` - Inicia una radio basada en los gustos del grupo en llamada.\n"
                  "`td?radio <0.0-1.0>` - Inicia radio automática basada en la canción actual.\n"
                  "`td?radio off` - Desactiva el modo radio.",
            inline=False
        )
        embed.add_field(
            name="📈 Estadísticas",
            value="`td?top` - Muestra el top global de canciones más escuchadas.\n"
                  "`td?historial` - Muestra las últimas canciones sonadas.",
            inline=False
        )
        embed.set_footer(text="Tooodles Music Bot • Escribe cualquier comando con el prefijo td?")
        await ctx.send(embed=embed)

    @tasks.loop(seconds=60)
    async def inactivity_check(self):
        if self.voice_client and not self.voice_client.is_playing() and not self.song_queue:
            await self.voice_client.disconnect()
            self.voice_client = None
            self.current_song = None
            print("✅ Desconectado por inactividad.")

    @commands.command()
    async def radio(self, ctx, *, arg: str = "0.75"):
        """Activa o desactiva el modo radio automática."""
        if arg.lower() == "off":
            self.radio_mode = False
            self.radio_seed_id = None
            await ctx.send("🛑 Modo radio desactivado.")
            return

        try:
            temperatura = float(arg)
        except ValueError:
            await ctx.send("❌ Parámetro inválido. Usa un número entre 0.0 y 1.0 o 'off'.")
            return

        if not self.current_song:
            await ctx.send("⚠️ No hay ninguna canción reproduciéndose para iniciar el modo radio.")
            return

        if not self.sp:
            await ctx.send("❌ Spotify no está disponible para recomendaciones.")
            return

        title = self.current_song['title']
        results = self.sp.search(q=title, type='track', limit=1)
        if not results or not results.get('tracks', {}).get('items'):
            await ctx.send("❌ No se encontró la canción en Spotify para generar recomendaciones.")
            return

        self.radio_seed_id = results['tracks']['items'][0]['id']
        self.radio_mode = True
        self.radio_temperature = max(0.0, min(temperatura, 1.0))
        await ctx.send(f"🔁 Modo radio activado (temperatura {self.radio_temperature:.2f}). Se añadirán canciones recomendadas automáticamente.")
        await self.expand_radio_queue(ctx)

    async def expand_radio_queue(self, ctx, seed_id=None, temperature=0.75):
        try:
            if not seed_id:
                seed_id = self.radio_seed_id
            if not seed_id or not self.sp:
                await ctx.send("❌ No se pudo obtener semilla de recomendación de Spotify.")
                return

            recs = self.sp.recommendations(
                seed_tracks=[seed_id],
                limit=5,
                target_valence=temperature,
                target_energy=temperature
            )
            await ctx.send("🎧 Añadiendo canciones sugeridas al modo radio...")

            for track in recs.get('tracks', []):
                title = track['name']
                artist = track['artists'][0]['name']
                query = f"{title} {artist}"
                await self.add_from_youtube(ctx, query, origin="🔁 Radio Automática")

        except Exception as e:
            await ctx.send(f"⚠️ Error al expandir la cola de radio: {e}")

async def setup(bot):
    await bot.add_cog(MusicCore(bot))
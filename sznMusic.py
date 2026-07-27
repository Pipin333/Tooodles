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

async def prefetch_chunk_throttled(song_info: dict) -> str | None:
    """
    Descarga los primeros 2 MB del stream de audio a /tmp/cache_{id}.webm
    en bloques pequeños de 64 KB con micro-pausas (throttling) para NO saturar
    la CPU ni el socket de red durante la reproducción actual.
    """
    song_id = song_info.get('id')
    stream_url = song_info.get('url')
    if not song_id or not stream_url or not stream_url.startswith("http"):
        return None

    cache_path = os.path.join(tempfile.gettempdir(), f"cache_{song_id}.webm")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) >= 2000000:
        song_info['cache_path'] = cache_path
        return cache_path

    headers = {
        "Range": "bytes=0-2097152",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    try:
        import aiohttp
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(stream_url, timeout=10) as resp:
                if resp.status in (200, 206):
                    with open(cache_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            f.write(chunk)
                            await asyncio.sleep(0.02)  # Micro-pausa de 20ms para ceder CPU al event loop

                    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
                        song_info['cache_path'] = cache_path
                        print(f"⚡ Chunk de 2MB precargado suavemente en caché: {cache_path}", flush=True)
                        return cache_path
    except Exception as e:
        print(f"ℹ️ Precarga suave omitida para {song_id}: {e}", flush=True)

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

    def schedule_queue_optimizations(self):
        """
        Estrategia Híbrida de Rendimiento:
        - Canción 2 (Siguiente en cola): Precarga suave de 2MB a disco (throttled 64KB/20ms).
        - Canciones 3 en adelante: Pre-resolución de URLs en segundo plano.
        """
        if not self.song_queue:
            return

        next_song = self.song_queue[0]
        if not next_song.get('cache_path'):
            self.bot.loop.create_task(prefetch_chunk_throttled(next_song))

        for song in self.song_queue[1:]:
            if not song.get('url'):
                async def _preresolve(s=song):
                    try:
                        resolved = await extract_info(s['title'])
                        s.update(resolved)
                    except Exception:
                        pass
                self.bot.loop.create_task(_preresolve())

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
        else:
            self.schedule_queue_optimizations()

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
            if not items:
                await ctx.send("📭 La playlist de Spotify está vacía.")
                return

            valid_items = [i for i in items if i.get('track')]
            await ctx.send(f"⚡ Carga ultrarrápida de playlist Spotify ({len(valid_items)} canciones)...")

            # 1. Resolver y reproducir el tema 1 de inmediato (<400ms)
            first_track = valid_items[0]['track']
            first_query = f"{first_track['name']} {first_track['artists'][0]['name']}"
            await self.add_from_youtube(ctx, first_query, origin=f"🎵 Playlist por {ctx.author.name}")

            # 2. Agregar los temas restantes 2..N a la cola de forma instantánea
            for item in valid_items[1:]:
                track = item['track']
                title = f"{track['name']} - {track['artists'][0]['name']}"
                song_dict = {
                    'title': title,
                    'url': None,
                    'duration': int(track.get('duration_ms', 0) / 1000),
                    'uploader': track['artists'][0]['name'],
                    'origin': f"🎵 Playlist por {ctx.author.name}"
                }
                self.song_queue.append(song_dict)

            # 3. Disparar pre-resolución de los siguientes temas en segundo plano
            self.schedule_queue_optimizations()

        except Exception as e:
            print(f"❌ Error al procesar playlist de Spotify: {e}", flush=True)
            await ctx.send("❌ Error al cargar la playlist de Spotify.")

    async def play_next(self, ctx):
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            return

        # Si el modo radio está activo y la cola tiene menos de 2 canciones, rellenar 5 más automáticamente
        if getattr(self, 'radio_mode', False) and len(self.song_queue) < 2:
            await self.expand_radio_queue(ctx)

        if not self.song_queue:
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

        # Ejecutar estrategia de optimización para los siguientes temas en la cola
        self.schedule_queue_optimizations()

        if not self.current_song.get('url') and not self.current_song.get('cache_path'):
            try:
                resolved = await extract_info(self.current_song['title'])
                self.current_song.update(resolved)
            except Exception as e:
                print(f"⚠️ Error al resolver tema de cola: {e}", flush=True)
                self.current_song = None
                return await self.play_next(ctx)

        target_path = self.current_song.get('cache_path')
        if not target_path or not os.path.exists(target_path):
            target_path = self.current_song.get('url')

        before_opts = '-probesize 32k -analyzeduration 0'
        if target_path and target_path.startswith("http"):
            before_opts += ' -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        ffmpeg_options = '-vn -threads 2'

        def after_playing(error):
            if error:
                print(f"⚠️ Error en reproducción de FFmpeg: {error}", flush=True)
            if self.current_song and self.current_song.get('title'):
                self.last_played_title = self.current_song['title']
                if not hasattr(self, 'radio_history'):
                    self.radio_history = []
                self.radio_history.append(self.current_song['title'].lower())
                if len(self.radio_history) > 30:
                    self.radio_history.pop(0)
            cleanup_cache(self.current_song)
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

    async def expand_radio_queue(self, ctx, seed_id: str | None = None, seed_title: str | None = None) -> bool:
        """
        Genera 5 canciones recomendadas distintas en lote y las añade a la cola.
        Usa Spotify search por artista/género como fuente principal.
        """
        if not hasattr(self, 'radio_history'):
            self.radio_history = []

        target_title = seed_title or getattr(self, 'last_played_title', None)
        if not target_title and self.current_song:
            target_title = self.current_song.get('title')

        # Agregar la canción actual al historial ANTES de buscar, para no recomendarla
        if self.current_song and self.current_song.get('title'):
            curr_lower = self.current_song['title'].lower()
            if curr_lower not in self.radio_history:
                self.radio_history.append(curr_lower)
        if target_title:
            tt_lower = target_title.lower()
            if tt_lower not in self.radio_history:
                self.radio_history.append(tt_lower)

        # También bloquear lo que ya está en cola
        queued_titles = [s['title'].lower() for s in self.song_queue if s.get('title')]

        print(f"📻 [RADIO DEBUG] target_title='{target_title}'", flush=True)
        print(f"📻 [RADIO DEBUG] radio_history ({len(self.radio_history)}): {self.radio_history[-5:]}", flush=True)
        print(f"📻 [RADIO DEBUG] queued_titles ({len(queued_titles)}): {queued_titles[:5]}", flush=True)

        recommended_titles = []

        # 1. Intentar Spotify Recommendations API
        if self.sp and target_title:
            try:
                # Primero buscar el track en Spotify para obtener artista
                search_result = self.sp.search(q=target_title, type='track', limit=1)
                seed_track = None
                seed_artist = None
                if search_result and search_result.get('tracks', {}).get('items'):
                    seed_track = search_result['tracks']['items'][0]
                    seed_artist = seed_track['artists'][0]['name']
                    print(f"📻 [RADIO DEBUG] Spotify encontró: '{seed_track['name']}' by '{seed_artist}' (id: {seed_track['id']})", flush=True)

                # Intentar sp.recommendations()
                if seed_track:
                    try:
                        recs = self.sp.recommendations(seed_tracks=[seed_track['id']], limit=20)
                        tracks = recs.get('tracks', [])
                        print(f"📻 [RADIO DEBUG] sp.recommendations() devolvió {len(tracks)} tracks", flush=True)
                        for t in tracks:
                            full_name = f"{t['name']} - {t['artists'][0]['name']}"
                            if self._radio_is_unique(full_name, recommended_titles, queued_titles):
                                recommended_titles.append(full_name)
                                if len(recommended_titles) >= 5:
                                    break
                    except Exception as rec_err:
                        print(f"⚠️ [RADIO] sp.recommendations() falló (posiblemente deprecado): {rec_err}", flush=True)

                # Si recommendations no dio suficientes, buscar por artista relacionado
                if len(recommended_titles) < 5 and seed_artist:
                    try:
                        artist_search = self.sp.search(q=f"artist:{seed_artist}", type='track', limit=30)
                        artist_tracks = artist_search.get('tracks', {}).get('items', [])
                        random.shuffle(artist_tracks)
                        print(f"📻 [RADIO DEBUG] Búsqueda por artista '{seed_artist}' devolvió {len(artist_tracks)} tracks", flush=True)
                        for t in artist_tracks:
                            full_name = f"{t['name']} - {t['artists'][0]['name']}"
                            if self._radio_is_unique(full_name, recommended_titles, queued_titles):
                                recommended_titles.append(full_name)
                                if len(recommended_titles) >= 5:
                                    break
                    except Exception as art_err:
                        print(f"⚠️ [RADIO] Búsqueda por artista falló: {art_err}", flush=True)

                # Si aún faltan, buscar por género/estilo similar
                if len(recommended_titles) < 5 and seed_artist:
                    try:
                        genre_queries = [
                            f"{seed_artist} similar",
                            f"genre:{seed_artist}",
                            seed_artist.split()[0] if ' ' in seed_artist else f"{seed_artist} rock"
                        ]
                        for gq in genre_queries:
                            if len(recommended_titles) >= 5:
                                break
                            genre_results = self.sp.search(q=gq, type='track', limit=15)
                            genre_tracks = genre_results.get('tracks', {}).get('items', [])
                            random.shuffle(genre_tracks)
                            for t in genre_tracks:
                                full_name = f"{t['name']} - {t['artists'][0]['name']}"
                                if self._radio_is_unique(full_name, recommended_titles, queued_titles):
                                    recommended_titles.append(full_name)
                                    if len(recommended_titles) >= 5:
                                        break
                    except Exception as genre_err:
                        print(f"⚠️ [RADIO] Búsqueda por género falló: {genre_err}", flush=True)

            except Exception as sp_err:
                print(f"⚠️ [RADIO] Error general de Spotify: {sp_err}", flush=True)

        # 2. Fallback: canciones de la Base de Datos (orden aleatorio)
        if len(recommended_titles) < 5:
            print(f"📻 [RADIO DEBUG] Fallback a BD (tenemos {len(recommended_titles)} de Spotify)", flush=True)
            with get_db_session() as session:
                songs = session.query(Song).all()
                if songs:
                    shuffled_db = list(songs)
                    random.shuffle(shuffled_db)
                    for s in shuffled_db:
                        if self._radio_is_unique(s.title, recommended_titles, queued_titles):
                            recommended_titles.append(s.title)
                            if len(recommended_titles) >= 5:
                                break

        if not recommended_titles:
            await ctx.send("⚠️ No se pudieron generar recomendaciones para la radio.")
            self.radio_mode = False
            return False

        print(f"📻 [RADIO DEBUG] Recomendaciones finales: {recommended_titles}", flush=True)
        await ctx.send(f"📻 **Radio Automática**: Añadidos **{len(recommended_titles)}** temas sugeridos a la cola.")

        for title in recommended_titles:
            self.radio_history.append(title.lower())
            if len(self.radio_history) > 30:
                self.radio_history.pop(0)

            song_dict = {
                'title': title,
                'url': None,
                'duration': 0,
                'uploader': 'Radio Automática',
                'origin': '📻 Radio Automática'
            }
            self.song_queue.append(song_dict)

        self.schedule_queue_optimizations()
        return True

    def _radio_is_unique(self, candidate: str, recommended: list, queued: list) -> bool:
        """Verifica que un candidato no sea duplicado del historial, cola actual ni recomendaciones ya elegidas."""
        c_lower = candidate.lower()
        # Extraer solo el nombre de la canción (antes del " - ")
        c_song_name = c_lower.split(" - ")[0].strip() if " - " in c_lower else c_lower

        # Verificar contra historial de radio
        for h in self.radio_history:
            h_song_name = h.split(" - ")[0].strip() if " - " in h else h
            if c_song_name in h_song_name or h_song_name in c_song_name:
                return False

        # Verificar contra cola actual
        for q in queued:
            q_song_name = q.split(" - ")[0].strip() if " - " in q else q
            if c_song_name in q_song_name or q_song_name in c_song_name:
                return False

        # Verificar contra recomendaciones ya elegidas en este batch
        for r in recommended:
            r_lower = r.lower()
            r_song_name = r_lower.split(" - ")[0].strip() if " - " in r_lower else r_lower
            if c_song_name in r_song_name or r_song_name in c_song_name:
                return False

        return True

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
        self.radio_mode = False
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
        self.schedule_queue_optimizations()
        await ctx.send("🔀 Cola de canciones mezclada aleatoriamente.")

    @commands.command()
    async def remove(self, ctx, index: int):
        """Elimina una canción específica de la cola según su número."""
        if index < 1 or index > len(self.song_queue):
            await ctx.send(f"❌ Número fuera de rango. La cola tiene {len(self.song_queue)} canciones.")
            return
        removed = self.song_queue.pop(index - 1)
        cleanup_cache(removed)
        self.schedule_queue_optimizations()
        await ctx.send(f"🗑️ Canción eliminada de la cola: **{removed['title']}**")

    @commands.command()
    async def move(self, ctx, old_index: int, new_index: int):
        """Mueve una canción de una posición a otra en la cola."""
        if old_index < 1 or old_index > len(self.song_queue) or new_index < 1 or new_index > len(self.song_queue):
            await ctx.send("❌ Índices fuera de rango.")
            return
        song = self.song_queue.pop(old_index - 1)
        self.song_queue.insert(new_index - 1, song)
        self.schedule_queue_optimizations()
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
    async def radio(self, ctx, *, arg: str = "on"):
        """Activa o desactiva el modo radio automática."""
        if arg.lower() in ("off", "stop", "desactivar"):
            self.radio_mode = False
            self.radio_seed_id = None
            await ctx.send("🛑 Modo radio desactivado.")
            return

        if not self.current_song and not self.song_queue:
            await ctx.send("⚠️ Debe haber una canción reproduciéndose o en cola para iniciar el modo radio.")
            return

        self.radio_mode = True
        if self.current_song:
            self.last_played_title = self.current_song.get('title')

        await ctx.send("📻 **Modo radio activado**. Se generarán 5 recomendaciones a la cola.")
        await self.expand_radio_queue(ctx)

        if self.voice_client and not self.voice_client.is_playing() and not self.voice_client.is_paused():
            await self.play_next(ctx)

async def setup(bot):
    await bot.add_cog(MusicCore(bot))
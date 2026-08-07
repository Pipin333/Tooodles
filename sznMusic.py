import asyncio
import os
import random
import shutil
import tempfile
import time
import discord
from discord.ext import commands, tasks
from rapidfuzz import process, fuzz

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from database import add_or_update_song, get_db_session, Song, UserLike
from sznUtils import extract_info, fetch_stealth_cookies
from recsys.engine import RecSysEngine

SPOTIFY_CLIENT_ID = os.getenv('client_id')
SPOTIFY_CLIENT_SECRET = os.getenv('client_secret')

GENRE_EXPANSION = {
    # Hip Hop / Rap / Trap
    "chilean hip hop": ["latin hip hop", "spanish hip hop", "argentinian hip hop", "boom bap", "mexican hip hop", "rap latina"],
    "chilean rap": ["latin hip hop", "spanish hip hop", "argentinian hip hop", "boom bap", "rap latina"],
    "latin hip hop": ["chilean hip hop", "spanish hip hop", "argentinian hip hop", "rap latino", "mexican hip hop", "boom bap"],
    "rap latino": ["latin hip hop", "chilean hip hop", "mexican hip hop", "argentinian hip hop", "rap latina"],
    "trap latino": ["reggaeton", "urbano latino", "trap argentino", "dembow", "rkt"],
    "reggaeton": ["trap latino", "urbano latino", "dembow", "latin pop", "rkt"],
    "urbano latino": ["reggaeton", "trap latino", "latin pop", "dembow"],
    "trap argentino": ["trap latino", "urbano latino", "rkt"],
    
    # Rock / Indie / Alternative (Chilean, Argentinian, Mexican, Spanish)
    "chilean rock": ["rock en espanol", "argentinian rock", "latin alternative", "mexican rock", "spanish rock", "chilean indie"],
    "chilean pop": ["chilean indie", "latin alternative", "latin pop", "synthpop"],
    "chilean indie": ["chilean pop", "latin alternative", "indie pop", "synthpop"],
    "rock en espanol": ["chilean rock", "argentinian rock", "mexican rock", "spanish rock", "latin alternative", "rock nacional"],
    "argentinian rock": ["rock en espanol", "chilean rock", "mexican rock", "spanish rock", "latin alternative", "rock nacional"],
    "rock nacional": ["argentinian rock", "rock en espanol", "latin alternative", "chilean rock"],
    "mexican rock": ["rock en espanol", "latin alternative", "argentinian rock", "spanish rock"],
    "spanish rock": ["rock en espanol", "latin alternative", "argentinian rock", "mexican rock"],
    "latin alternative": ["rock en espanol", "chilean rock", "argentinian rock", "mexican rock", "spanish rock", "chilean indie"],
    
    # Reggae / Ska
    "reggae en espanol": ["reggae fusion", "latin alternative", "ska", "spanish reggae"],
    "reggae fusion": ["reggae en espanol", "ska", "dancehall"],
    "ska": ["ska argentino", "reggae en espanol", "latin alternative"],
    
    # Pop / Synthpop / Indie Pop
    "synthpop": ["indie pop", "latin alternative", "dance pop"],
    "indie pop": ["synthpop", "latin alternative", "indie rock"],
    
    # Metal
    "metal": ["heavy metal", "thrash metal", "groove metal", "latin metal"],
    "thrash metal": ["speed metal", "heavy metal", "death metal"],
    "heavy metal": ["hard rock", "thrash metal", "power metal"]
}


async def prefetch_chunk_throttled(song_info: dict) -> str | None:
    """
    Descarga el stream de audio completo a /tmp/cache_{id}.webm en segundo plano.
    Usa un archivo temporal (.tmp) y lo renombra al finalizar para asegurar
    que FFmpeg nunca lea un archivo truncado (lo que causaría saltos de canción).
    """
    url = song_info.get('url')
    song_id = song_info.get('id')
    duration = song_info.get('duration', 0)

    # Omitir precarga si el tema dura más de 10 minutos (600s) o no tiene URL
    if not url or not song_id or duration > 600:
        return None

    temp_dir = tempfile.gettempdir()
    final_cache_path = os.path.join(temp_dir, f"cache_{song_id}.webm")
    temp_cache_path = os.path.join(temp_dir, f"cache_{song_id}.tmp")

    if os.path.exists(final_cache_path) and os.path.getsize(final_cache_path) > 1024:
        return final_cache_path

    from sznUtils import get_cookie_file_path
    node_path = shutil.which("node") or shutil.which("nodejs") or "/usr/bin/node"
    cookie_path = get_cookie_file_path()

    def _download():
        from yt_dlp import YoutubeDL
        ydl_opts = {
            "format": "bestaudio/best/ba",
            "outtmpl": temp_cache_path,
            "quiet": True,
            "nocheckcertificate": True,
            "cookiefile": cookie_path if cookie_path else None,
            "js_runtimes": {"node": {"path": node_path}},
            "remote_components": ["ejs:github"],
            "extractor_args": {"youtube": {"player_client": ["mweb", "android_creator", "web"]}}
        }
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    try:
        print(f"⚡ Iniciando descarga completa de audio en segundo plano: {song_info.get('title')}", flush=True)
        await asyncio.to_thread(_download)
        if os.path.exists(temp_cache_path) and os.path.getsize(temp_cache_path) > 1024:
            os.replace(temp_cache_path, final_cache_path)
            song_info['cache_path'] = final_cache_path
            print(f"⚡ Audio completo precargado en caché: {final_cache_path}", flush=True)
            return final_cache_path
    except Exception as e:
        print(f"⚠️ Precarga de audio completa falló: {e}", flush=True)
        if os.path.exists(temp_cache_path):
            try:
                os.remove(temp_cache_path)
            except Exception:
                pass

    return None

def cleanup_cache(song_info: dict | None = None):
    """Limpia los archivos parciales y completos en /tmp."""
    try:
        if song_info and song_info.get('id'):
            temp_dir = tempfile.gettempdir()
            cache_path = os.path.join(temp_dir, f"cache_{song_info['id']}.webm")
            temp_path = os.path.join(temp_dir, f"cache_{song_info['id']}.tmp")
            if os.path.exists(cache_path):
                os.remove(cache_path)
                print(f"🧹 Cache completo eliminado: {cache_path}", flush=True)
            if os.path.exists(temp_path):
                os.remove(temp_path)
        else:
            temp_dir = tempfile.gettempdir()
            for fname in os.listdir(temp_dir):
                if fname.startswith("cache_") and (fname.endswith(".webm") or fname.endswith(".tmp")):
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


class GuildPlayer:
    """Encapsula el estado de reproducción y cola de un servidor individual."""
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.song_queue = []
        self.current_song = None
        self.voice_client = None
        self.radio_seed_id = None
        self.radio_mode = False
        self.radio_temperature = 0.75
        self.is_loading_song = False
        self.recent_artist_ids = []
        self.last_played_title = None
        self.last_ctx = None
        self.current_song_start_time = 0
        self.current_song_skipped = False
        self.radio_history = []
        self.autoplay_mode = False  # Modo autoplay con RecSys ML
        self.play_lock = asyncio.Lock()


class MusicCore(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}  # guild_id -> GuildPlayer

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

        # Inicializar motor de recomendación ML
        self.recsys_engine = RecSysEngine()
        try:
            self.recsys_engine.load()
        except Exception as e:
            print(f"ℹ️ [RecSys] Motor de recomendación no disponible: {e}")

        try:
            self.inactivity_check.start()
        except Exception as e:
            print(f"❌ Error al iniciar inactivity_check: {e}")

        try:
            self.recsys_training_loop.start()
        except Exception as e:
            print(f"⚠️ [RecSys] Error al iniciar loop de entrenamiento: {e}")

    def get_player(self, target) -> GuildPlayer:
        """Obtiene o crea la instancia de GuildPlayer asociada al servidor."""
        if isinstance(target, int):
            guild_id = target
        elif hasattr(target, 'guild') and target.guild:
            guild_id = target.guild.id
        elif hasattr(target, 'id'):
            guild_id = target.id
        else:
            guild_id = 0

        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(guild_id)
        return self.players[guild_id]

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
        player = self.get_player(ctx)

        if ctx.guild.voice_client:
            player.voice_client = ctx.guild.voice_client
            if player.voice_client.channel != target_channel:
                await player.voice_client.move_to(target_channel)
            return player.voice_client

        try:
            print(f"🔊 Conectando al canal de voz: {target_channel.name} en {ctx.guild.name}...")
            player.voice_client = await target_channel.connect(timeout=15.0, reconnect=True)
            
            # Cargar el modo radio por defecto al conectar
            from sznUtils import load_config, load_guild_queue, is_guild_persist_enabled
            default_radio = load_config(f"default_radio_{ctx.guild.id}")
            player.radio_mode = (default_radio == "on")
            
            # Restaurar cola persistente si está activada para este servidor
            persist_enabled = is_guild_persist_enabled(ctx.guild.id)
            print(f"🔍 [JOIN] Guild {ctx.guild.id} — persist={persist_enabled}, song_queue_actual={len(player.song_queue)}", flush=True)
            if persist_enabled and not player.song_queue:
                restored = await asyncio.to_thread(load_guild_queue, ctx.guild.id)
                print(f"🔍 [JOIN] load_guild_queue devolvió {len(restored)} canciones para guild {ctx.guild.id}", flush=True)
                if restored:
                    player.song_queue.extend(restored)
                    await ctx.send(f"📥 **Cola recuperada**: Se restauraron **{len(restored)}** canciones pendientes tras el reinicio.")
                    self.schedule_queue_optimizations(ctx)
                    if not player.current_song and not (player.voice_client and player.voice_client.is_playing()):
                        self.bot.loop.create_task(self.play_next(ctx))
                else:
                    print(f"⚠️ [JOIN] No había cola guardada en BD para guild {ctx.guild.id}", flush=True)

            return player.voice_client
        except Exception as e:
            print(f"❌ Error de conexión al canal de voz: {e}")
            await ctx.send("❌ Error al conectar al canal de voz.")
            return None

    def schedule_queue_optimizations(self, target):
        """
        Estrategia de Precarga Inteligente a los 30 Segundos:
        Espera 30 segundos tras iniciar la reproducción de la canción actual para resolver
        y precargar únicamente la siguiente canción en la cola (N+1).
        Elimina por completo las peticiones en ráfaga y evita bloqueos anti-bot de YouTube.
        """
        player = self.get_player(target)
        if not player.song_queue:
            return

        next_song = player.song_queue[0]
        if next_song.get('cache_path') or next_song.get('_is_optimizing'):
            return

        next_song['_is_optimizing'] = True

        async def _delayed_optimize(s=next_song, p=player):
            try:
                # Esperar 30 segundos de reproducción continua
                await asyncio.sleep(30)
                
                # Verificar que la canción sigue siendo la siguiente en la cola
                if p.song_queue and p.song_queue[0] is s:
                    if not s.get('url'):
                        resolved = await extract_info(s['title'])
                        if resolved:
                            s.update(resolved)
                    if s.get('url'):
                        cached_file = await prefetch_chunk_throttled(s)
                        if cached_file:
                            s['cache_path'] = cached_file
            except Exception as e:
                print(f"⚠️ Error en precarga diferida a los 30s: {e}", flush=True)
            finally:
                s['_is_optimizing'] = False

        self.bot.loop.create_task(_delayed_optimize())

    async def add_song_dict(self, ctx, song_info: dict, origin: str = "🎵 Solicitada", notify: bool = True):
        player = self.get_player(ctx)
        song_info['origin'] = origin
        if ctx:
            if ctx.author:
                song_info['user_id'] = str(ctx.author.id)
                song_info['username'] = ctx.author.name
            if ctx.guild:
                song_info['guild_id'] = str(ctx.guild.id)
        
        # Insertar canciones del usuario antes de las canciones recomendadas por la radio
        insert_idx = len(player.song_queue)
        for idx, song in enumerate(player.song_queue):
            if song.get('origin') == "📻 Radio Automática":
                insert_idx = idx
                break
        player.song_queue.insert(insert_idx, song_info)

        try:
            add_or_update_song(song_info['title'], song_info.get('id') or song_info['title'], duration=song_info.get('duration', 0))
        except Exception as e:
            print(f"⚠️ No se pudo guardar la canción en BD: {e}")

        vc = player.voice_client
        is_busy = vc and (vc.is_playing() or vc.is_paused())

        # Solo enviar mensaje de "Añadido a la cola" si ya hay una canción sonando y notify=True
        if notify and is_busy:
            await ctx.send(f"🎶 Añadido a la cola: **{song_info['title']}** ({self.format_duration(song_info.get('duration', 0))})")

        if not player.current_song and not is_busy:
            await self.play_next(ctx)
        else:
            self.schedule_queue_optimizations(ctx)

    async def add_from_youtube(self, ctx, query, origin="🎵 Búsqueda de YouTube", notify: bool = True):
        player = self.get_player(ctx)
        player.is_loading_song = True
        try:
            info = await extract_info(query)
            await self.add_song_dict(ctx, info, origin, notify=notify)
        except Exception as e:
            print(f"❌ Error interno en la búsqueda/extracción: {e}", flush=True)
            await ctx.send("❌ No se pudo procesar o encontrar la canción solicitada.")
        finally:
            player.is_loading_song = False

    async def add_from_spotify(self, ctx, url, track_id=None):
        import re
        if not track_id:
            match = re.search(r'track[/:]([a-zA-Z0-9]+)', url)
            track_id = match.group(1) if match else url.split("/")[-1].split("?")[0]
        
        query = None
        if self.sp and track_id:
            try:
                track = await asyncio.to_thread(self.sp.track, track_id)
                query = f"{track['name']} {track['artists'][0]['name']}"
            except Exception as e:
                print(f"⚠️ Error al obtener track de Spotify API: {e}", flush=True)

        if not query and track_id:
            try:
                import aiohttp
                oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(oembed_url, timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            title = data.get('title')
                            artist = data.get('author_name', '')
                            if title:
                                query = f"{title} {artist}".strip()
            except Exception as e:
                print(f"⚠️ Error en oEmbed de Spotify: {e}", flush=True)

        if query:
            await self.add_from_youtube(ctx, query, origin=f"🎵 Spotify por {ctx.author.name}")
        else:
            await ctx.send("❌ No se pudo procesar la canción de Spotify.")

    async def add_playlist_from_spotify(self, ctx, url, playlist_id=None):
        if not self.sp:
            await ctx.send("❌ La API de Spotify no está configurada.")
            return
        try:
            import re
            if not playlist_id:
                match = re.search(r'playlist[/:]([a-zA-Z0-9]+)', url)
                playlist_id = match.group(1) if match else url.split("/")[-1].split("?")[0]

            player = self.get_player(ctx)

            # Recolectar TODOS los tracks paginando la API de Spotify (límite: 100 por página)
            all_items = []
            results = await asyncio.to_thread(self.sp.playlist_tracks, playlist_id, limit=100)
            while results:
                all_items.extend(results.get('items', []))
                results = await asyncio.to_thread(self.sp.next, results) if results.get('next') else None

            valid_items = [i for i in all_items if i.get('track') and i['track'].get('name')]
            if not valid_items:
                await ctx.send("📭 La playlist de Spotify está vacía.")
                return

            await ctx.send(f"⚡ Carga de playlist Spotify ({len(valid_items)} canciones)...")

            first_track = valid_items[0]['track']
            first_query = f"{first_track['name']} {first_track['artists'][0]['name']}"
            await self.add_from_youtube(ctx, first_query, origin=f"🎵 Playlist por {ctx.author.name}", notify=False)

            for item in valid_items[1:]:
                track = item['track']
                if not track or not track.get('name'):
                    continue
                title = f"{track['name']} - {track['artists'][0]['name']}"
                song_dict = {
                    'title': title,
                    'url': None,
                    'duration': int(track.get('duration_ms', 0) / 1000),
                    'uploader': track['artists'][0]['name'],
                    'origin': f"🎵 Playlist por {ctx.author.name}",
                    'user_id': str(ctx.author.id) if ctx and ctx.author else None,
                    'username': ctx.author.name if ctx and ctx.author else None,
                    'guild_id': str(ctx.guild.id) if ctx and ctx.guild else None
                }
                insert_idx = len(player.song_queue)
                for idx, song in enumerate(player.song_queue):
                    if song.get('origin') == "📻 Radio Automática":
                        insert_idx = idx
                        break
                player.song_queue.insert(insert_idx, song_dict)

            self.schedule_queue_optimizations(ctx)

        except Exception as e:
            print(f"❌ Error al procesar playlist de Spotify: {e}", flush=True)
            await ctx.send("❌ Error al cargar la playlist de Spotify.")

    async def add_album_from_spotify(self, ctx, url, album_id=None):
        if not self.sp:
            await ctx.send("❌ La API de Spotify no está configurada.")
            return
        try:
            import re
            if not album_id:
                match = re.search(r'album[/:]([a-zA-Z0-9]+)', url)
                album_id = match.group(1) if match else url.split("/")[-1].split("?")[0]

            player = self.get_player(ctx)
            album = await asyncio.to_thread(self.sp.album, album_id)
            album_name = album.get('name', 'Álbum')
            tracks = album.get('tracks', {}).get('items', [])
            if not tracks:
                await ctx.send("📭 El álbum de Spotify está vacío.")
                return

            await ctx.send(f"⚡ Carga de álbum Spotify **{album_name}** ({len(tracks)} canciones)...")

            first_track = tracks[0]
            first_artist = first_track['artists'][0]['name'] if first_track.get('artists') else ''
            first_query = f"{first_track['name']} {first_artist}".strip()
            await self.add_from_youtube(ctx, first_query, origin=f"🎵 Álbum Spotify por {ctx.author.name}", notify=False)

            for track in tracks[1:]:
                artist_name = track['artists'][0]['name'] if track.get('artists') else ''
                title = f"{track['name']} - {artist_name}"
                song_dict = {
                    'title': title,
                    'url': None,
                    'duration': int(track.get('duration_ms', 0) / 1000),
                    'uploader': artist_name,
                    'origin': f"🎵 Álbum Spotify por {ctx.author.name}",
                    'user_id': str(ctx.author.id) if ctx and ctx.author else None,
                    'username': ctx.author.name if ctx and ctx.author else None,
                    'guild_id': str(ctx.guild.id) if ctx and ctx.guild else None
                }
                insert_idx = len(player.song_queue)
                for idx, song in enumerate(player.song_queue):
                    if song.get('origin') == "📻 Radio Automática":
                        insert_idx = idx
                        break
                player.song_queue.insert(insert_idx, song_dict)

            self.schedule_queue_optimizations(ctx)

        except Exception as e:
            print(f"❌ Error al procesar álbum de Spotify: {e}", flush=True)
            await ctx.send("❌ Error al cargar el álbum de Spotify.")

    async def add_artist_from_spotify(self, ctx, url, artist_id=None):
        if not self.sp:
            await ctx.send("❌ La API de Spotify no está configurada.")
            return
        try:
            import re
            if not artist_id:
                match = re.search(r'artist[/:]([a-zA-Z0-9]+)', url)
                artist_id = match.group(1) if match else url.split("/")[-1].split("?")[0]

            player = self.get_player(ctx)
            artist = await asyncio.to_thread(self.sp.artist, artist_id)
            artist_name = artist.get('name', 'Artista')
            top_tracks_res = await asyncio.to_thread(self.sp.artist_top_tracks, artist_id)
            tracks = top_tracks_res.get('tracks', [])
            if not tracks:
                await ctx.send("📭 No se encontraron canciones para este artista.")
                return

            await ctx.send(f"⚡ Carga de Top Canciones de **{artist_name}** en Spotify ({len(tracks)} canciones)...")

            first_track = tracks[0]
            first_query = f"{first_track['name']} {artist_name}".strip()
            await self.add_from_youtube(ctx, first_query, origin=f"🎵 Top Artista por {ctx.author.name}", notify=False)

            for track in tracks[1:]:
                title = f"{track['name']} - {artist_name}"
                song_dict = {
                    'title': title,
                    'url': None,
                    'duration': int(track.get('duration_ms', 0) / 1000),
                    'uploader': artist_name,
                    'origin': f"🎵 Top Artista por {ctx.author.name}",
                    'user_id': str(ctx.author.id) if ctx and ctx.author else None,
                    'username': ctx.author.name if ctx and ctx.author else None,
                    'guild_id': str(ctx.guild.id) if ctx and ctx.guild else None
                }
                insert_idx = len(player.song_queue)
                for idx, song in enumerate(player.song_queue):
                    if song.get('origin') == "📻 Radio Automática":
                        insert_idx = idx
                        break
                player.song_queue.insert(insert_idx, song_dict)

            self.schedule_queue_optimizations(ctx)

        except Exception as e:
            print(f"❌ Error al procesar artista de Spotify: {e}", flush=True)
            await ctx.send("❌ Error al cargar canciones del artista de Spotify.")

    async def add_playlist_from_youtube(self, ctx, url):
        try:
            player = self.get_player(ctx)
            from sznUtils import extract_playlist_metadata
            items = await asyncio.to_thread(extract_playlist_metadata, url)
            
            if not items:
                await ctx.send("📭 No se pudieron extraer canciones de la playlist de YouTube.")
                return

            await ctx.send(f"⚡ Carga rápida de playlist YouTube ({len(items)} canciones)...")

            first_item = items[0]
            first_query = first_item.get('url') or first_item.get('title')
            await self.add_from_youtube(ctx, first_query, origin=f"🎵 Playlist YouTube por {ctx.author.name}", notify=False)

            for item in items[1:]:
                song_dict = {
                    'title': item['title'],
                    'url': item.get('url'),
                    'duration': item.get('duration', 0),
                    'uploader': item.get('uploader', 'YouTube'),
                    'origin': f"🎵 Playlist YouTube por {ctx.author.name}",
                    'user_id': str(ctx.author.id) if ctx and ctx.author else None,
                    'username': ctx.author.name if ctx and ctx.author else None,
                    'guild_id': str(ctx.guild.id) if ctx and ctx.guild else None,
                    'id': item.get('id')
                }
                insert_idx = len(player.song_queue)
                for idx, song in enumerate(player.song_queue):
                    if song.get('origin') == "📻 Radio Automática":
                        insert_idx = idx
                        break
                player.song_queue.insert(insert_idx, song_dict)

            self.schedule_queue_optimizations(ctx)

        except Exception as e:
            print(f"❌ Error al procesar playlist de YouTube: {e}", flush=True)
            await ctx.send("❌ Error al cargar la playlist de YouTube.")

    async def play_next(self, ctx):
        player = self.get_player(ctx)
        player.last_ctx = ctx

        async with player.play_lock:
            if player.voice_client and (player.voice_client.is_playing() or player.voice_client.is_paused()):
                return

            if getattr(player, 'radio_mode', False) and len(player.song_queue) < 2:
                await self.expand_radio_queue(ctx)

            # RecSys Autoplay: si la cola está vacía y autoplay está activado
            if not player.song_queue and getattr(player, 'autoplay_mode', False):
                await self._fill_queue_from_recsys(ctx, player)

            if not player.song_queue:
                await ctx.send("📭 La cola de canciones está vacía.")
                player.current_song = None
                return

            next_item = player.song_queue.pop(0)
            if not next_item or not isinstance(next_item, dict):
                player.current_song = None
                return

            player.current_song = next_item
            self.record_played_track(player.current_song.get('title', ''), ctx)

            ui = self.bot.get_cog("MusicUI")
            if ui and player.current_song:
                await ui.notify_now_playing(ctx, player.current_song)

            musicdb = getattr(self.bot, "musicdb", None)
            if musicdb and player.current_song.get('title'):
                musicdb.log_song(player.current_song['title'])

            self.schedule_queue_optimizations(ctx)

            curr_url = player.current_song.get('url') or ''
            has_valid_stream = curr_url.startswith("http") and ("googlevideo.com" in curr_url or "soundgasm" in curr_url or ".webm" in curr_url or ".m4a" in curr_url or ".mp3" in curr_url)
            has_valid_cache = bool(player.current_song.get('cache_path') and os.path.exists(player.current_song['cache_path']))

            if not has_valid_stream and not has_valid_cache:
                try:
                    search_query = player.current_song.get('url') or player.current_song.get('title', '')
                    resolved = await extract_info(search_query)
                    if resolved and resolved.get('url'):
                        player.current_song.update(resolved)
                    else:
                        raise RuntimeError("Failed to resolve audio stream URL.")
                except Exception as e:
                    print(f"⚠️ Error al resolver tema de cola ({player.current_song.get('title')}): {e}", flush=True)
                    player.current_song = None
                    return

            target_path = player.current_song.get('cache_path')
            if not target_path or not os.path.exists(target_path):
                target_path = player.current_song.get('url')

            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            before_opts = f'-probesize 64k -analyzeduration 0 -user_agent "{user_agent}"'
            if target_path and target_path.startswith("http"):
                before_opts += ' -headers "Referer: https://www.youtube.com/\r\n" -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 3'
            ffmpeg_options = '-vn -threads 2'

            def after_playing(error):
                if error:
                    print(f"⚠️ Error en reproducción de FFmpeg: {error}", flush=True)

                asyncio.run_coroutine_threadsafe(self._process_after_playing(ctx, player.current_song, getattr(player, 'current_song_start_time', None), getattr(player, 'current_song_skipped', False)), self.bot.loop)

            try:
                player.current_song_start_time = time.time()
                player.current_song_skipped = False
                audio_source = discord.FFmpegPCMAudio(target_path, before_options=before_opts, options=ffmpeg_options)
                if player.voice_client and player.voice_client.is_connected():
                    if player.voice_client.is_playing():
                        print("⚠️ Voice client ya estaba reproduciendo audio. Cancelando reproducción duplicada.", flush=True)
                        return
                    player.voice_client.play(audio_source, after=after_playing)
                else:
                    await ctx.send("⚠️ El bot fue desconectado del canal de voz.")
            except Exception as e:
                print(f"❌ Error al iniciar FFmpeg: {e}", flush=True)
                player.current_song = None

    async def _process_after_playing(self, ctx, current_song, start_time, skipped):
        player = self.get_player(ctx)
        if current_song:
            try:
                listened_duration = int(time.time() - start_time) if start_time else 0
                completed = not skipped
                
                from database import log_play_event
                await asyncio.to_thread(
                    log_play_event,
                    title=current_song.get('title'),
                    artist=current_song.get('uploader') or current_song.get('title'),
                    duration=current_song.get('duration', 0),
                    user_id=current_song.get('user_id'),
                    username=current_song.get('username') or "Desconocido",
                    guild_id=current_song.get('guild_id'),
                    listened_duration=listened_duration,
                    completed=completed,
                    skipped_at=listened_duration if skipped else None
                )
            except Exception as db_err:
                print(f"⚠️ Error al registrar telemetría de reproducción: {db_err}", flush=True)

        if current_song and current_song.get('title'):
            player.last_played_title = current_song['title']
            player.radio_history.append(current_song['title'].lower())
            if len(player.radio_history) > 30:
                player.radio_history.pop(0)

        await asyncio.to_thread(cleanup_cache, current_song)
        player.current_song = None
        await self.play_next(ctx)

    def _clean_title_for_search(self, title: str) -> tuple[str, str | None]:
        import re
        clean = title
        patterns_to_remove = [
            r'\(Official\s*(Music\s*)?Video\)',
            r'\(Video\s*Version\)',
            r'\(Official\s*Audio\)',
            r'\(Official\s*Lyric\s*Video\)',
            r'\(Official\s*Visualizer\)',
            r'\(Lyrics?\)',
            r'\(Audio\)',
            r'\(Live\)',
            r'\(HD\)',
            r'\(HQ\)',
            r'\(Remastered\s*\d*\)',
            r'\[Official\s*(Music\s*)?Video\]',
            r'\[Lyrics?\]',
            r'\[Audio\]',
            r'\[HD\]',
            r'\[HQ\]',
            r'\[Remastered\s*\d*\]',
        ]
        for p in patterns_to_remove:
            clean = re.sub(p, '', clean, flags=re.IGNORECASE)
        clean = clean.strip().rstrip('-').strip()

        artist = None
        if ' - ' in clean:
            parts = clean.split(' - ', 1)
            artist = parts[0].strip()
            clean = parts[1].strip()
        elif ' – ' in clean:
            parts = clean.split(' – ', 1)
            artist = parts[0].strip()
            clean = parts[1].strip()

        return clean, artist

    async def expand_radio_queue(self, ctx, seed_id: str | None = None, seed_title: str | None = None) -> bool:
        player = self.get_player(ctx)
        target_title = seed_title or getattr(player, 'last_played_title', None)
        if not target_title and player.current_song:
            target_title = player.current_song.get('title')

        if player.current_song and player.current_song.get('title'):
            curr_lower = player.current_song['title'].lower()
            if curr_lower not in player.radio_history:
                player.radio_history.append(curr_lower)
        if target_title:
            tt_lower = target_title.lower()
            if tt_lower not in player.radio_history:
                player.radio_history.append(tt_lower)

        queued_titles = [s['title'].lower() for s in player.song_queue if s.get('title')]
        clean_song, extracted_artist = self._clean_title_for_search(target_title) if target_title else ("", None)

        print(f"📻 [RADIO DEBUG] [{ctx.guild.name}] target_title='{target_title}'", flush=True)
        recommended_titles = []

        if self.sp and target_title:
            try:
                search_q = f"artist:{extracted_artist} track:{clean_song}" if extracted_artist else clean_song
                search_result = self.sp.search(q=search_q, type='track', limit=1)

                seed_artist = extracted_artist
                seed_artist_id = None
                if search_result and search_result.get('tracks', {}).get('items'):
                    found_track = search_result['tracks']['items'][0]
                    seed_artist = found_track['artists'][0]['name']
                    seed_artist_id = found_track['artists'][0]['id']
                else:
                    if seed_artist:
                        try:
                            artist_res = self.sp.search(q=f"artist:{seed_artist}", type='artist', limit=1)
                            if artist_res and artist_res.get('artists', {}).get('items'):
                                seed_artist_id = artist_res['artists']['items'][0]['id']
                                seed_artist = artist_res['artists']['items'][0]['name']
                        except Exception as e:
                            print(f"⚠️ [RADIO] Búsqueda de artista falló: {e}", flush=True)

                if seed_artist:
                    # Capa 1: Mismo artista
                    try:
                        artist_search = self.sp.search(q=f"artist:{seed_artist}", type='track', limit=30)
                        artist_tracks = artist_search.get('tracks', {}).get('items', [])
                        random.shuffle(artist_tracks)
                        for t in artist_tracks:
                            full_name = f"{t['name']} - {t['artists'][0]['name']}"
                            if self._radio_is_unique(player, full_name, recommended_titles, queued_titles):
                                recommended_titles.append(full_name)
                                if len(recommended_titles) >= 2:
                                    break
                    except Exception as e:
                        print(f"⚠️ [RADIO] Capa 1 falló: {e}", flush=True)

                    # Capa 2: Géneros consolidados
                    if len(recommended_titles) < 5:
                        try:
                            genres = []
                            artist_ids = getattr(player, 'recent_artist_ids', [])
                            if artist_ids:
                                try:
                                    artists_info = self.sp.artists(artist_ids)
                                    for artist in artists_info.get('artists', []):
                                        if artist and artist.get('genres'):
                                            genres.extend(artist['genres'])
                                except Exception as hist_err:
                                    print(f"⚠️ [RADIO] Error al consultar historial de artistas: {hist_err}", flush=True)

                            if not genres and seed_artist_id:
                                try:
                                    artist_info = self.sp.artist(seed_artist_id)
                                    genres = artist_info.get('genres', [])
                                except Exception as fallback_err:
                                    print(f"⚠️ [RADIO] Error en fallback de artista: {fallback_err}", flush=True)

                            expanded_genres = self._expand_genres(list(set(genres)))
                            if expanded_genres:
                                shuffled_genres = list(expanded_genres)
                                random.shuffle(shuffled_genres)
                                
                                for gen_name in shuffled_genres:
                                    if len(recommended_titles) >= 5:
                                        break
                                    try:
                                        gen_search = self.sp.search(q=f'genre:"{gen_name}"', type='track', limit=20)
                                        gen_tracks = gen_search.get('tracks', {}).get('items', [])
                                        if gen_tracks:
                                            gen_tracks = sorted(gen_tracks, key=lambda x: x.get('popularity', 0), reverse=True)
                                            popular_subset = gen_tracks[:10]
                                            random.shuffle(popular_subset)

                                            for t in popular_subset:
                                                t_artist = t['artists'][0]['name']
                                                already_rec = [r.split(" - ")[1].lower().strip() for r in recommended_titles if " - " in r]
                                                if t_artist.lower().strip() in already_rec:
                                                    continue

                                                full_name = f"{t['name']} - {t_artist}"
                                                if t_artist.lower() != seed_artist.lower() and self._radio_is_unique(player, full_name, recommended_titles, queued_titles):
                                                    recommended_titles.append(full_name)
                                                    break
                                    except Exception:
                                        pass
                        except Exception as artist_info_err:
                            print(f"⚠️ [RADIO] Error procesando géneros: {artist_info_err}", flush=True)

                    # Capa 3: Similar queries
                    if len(recommended_titles) < 5:
                        similar_queries = [
                            f"{seed_artist} similar artists",
                            f"fans also like {seed_artist}",
                            f"{clean_song} similar",
                        ]
                        for sq in similar_queries:
                            if len(recommended_titles) >= 5:
                                break
                            try:
                                sim_results = self.sp.search(q=sq, type='track', limit=20)
                                sim_tracks = sim_results.get('tracks', {}).get('items', [])
                                if sim_tracks:
                                    sim_tracks = sorted(sim_tracks, key=lambda x: x.get('popularity', 0), reverse=True)
                                    popular_subset = sim_tracks[:10]
                                    random.shuffle(popular_subset)

                                    for t in popular_subset:
                                        t_artist = t['artists'][0]['name']
                                        already_rec = [r.split(" - ")[1].lower().strip() for r in recommended_titles if " - " in r]
                                        if t_artist.lower().strip() in already_rec:
                                            continue

                                        full_name = f"{t['name']} - {t_artist}"
                                        if t_artist.lower() != seed_artist.lower() and self._radio_is_unique(player, full_name, recommended_titles, queued_titles):
                                            recommended_titles.append(full_name)
                                            break
                            except Exception:
                                pass
            except Exception as sp_err:
                print(f"⚠️ [RADIO] Error general de Spotify: {sp_err}", flush=True)

        # Fallback BD
        if len(recommended_titles) < 5:
            with get_db_session() as session:
                songs = session.query(Song).all()
                if songs:
                    shuffled_db = list(songs)
                    random.shuffle(shuffled_db)
                    for s in shuffled_db:
                        if self._radio_is_unique(player, s.title, recommended_titles, queued_titles):
                            recommended_titles.append(s.title)
                            if len(recommended_titles) >= 5:
                                break

        if not recommended_titles:
            await ctx.send("⚠️ No se pudieron generar recomendaciones para la radio.")
            player.radio_mode = False
            return False

        await ctx.send(f"📻 **Radio Automática**: Añadidos **{len(recommended_titles)}** temas sugeridos a la cola.")

        for title in recommended_titles:
            player.radio_history.append(title.lower())
            if len(player.radio_history) > 30:
                player.radio_history.pop(0)

            song_dict = {
                'title': title,
                'url': None,
                'duration': 0,
                'uploader': 'Radio Automática',
                'origin': '📻 Radio Automática',
                'guild_id': str(ctx.guild.id)
            }
            player.song_queue.append(song_dict)

        self.schedule_queue_optimizations(ctx)
        return True

    def _radio_is_unique(self, player: GuildPlayer, candidate: str, recommended: list, queued: list) -> bool:
        c_lower = candidate.lower()
        junk_keywords = [
            "chapter", "episode", "audiobook", "audio book", "podcast",
            "imagination audio", "volume", "vol.", "track", "intro",
            "outro", "skit", "interlude", "remastered"
        ]
        if any(jk in c_lower for jk in junk_keywords):
            return False

        c_song_name = c_lower.split(" - ")[0].strip() if " - " in c_lower else c_lower
        if c_song_name.isdigit():
            return False

        for h in player.radio_history:
            h_song_name = h.split(" - ")[0].strip() if " - " in h else h
            if c_song_name in h_song_name or h_song_name in c_song_name:
                return False

        for q in queued:
            q_song_name = q.split(" - ")[0].strip() if " - " in q else q
            if c_song_name in q_song_name or q_song_name in c_song_name:
                return False

        for r in recommended:
            r_lower = r.lower()
            r_song_name = r_lower.split(" - ")[0].strip() if " - " in r_lower else r_lower
            if c_song_name in r_song_name or r_song_name in c_song_name:
                return False

        return True

    def _expand_genres(self, genres_list: list[str]) -> list[str]:
        expanded = set()
        for g in genres_list:
            g_lower = g.lower().strip()
            expanded.add(g_lower)
            if g_lower in GENRE_EXPANSION:
                expanded.update(GENRE_EXPANSION[g_lower])
            else:
                if "rock" in g_lower:
                    expanded.update(["rock en espanol", "latin alternative", "classic rock"])
                if "hip hop" in g_lower or "rap" in g_lower:
                    expanded.update(["latin hip hop", "boom bap", "spanish hip hop"])
                if "pop" in g_lower:
                    expanded.update(["latin pop", "indie pop", "synthpop"])
                if "reggaeton" in g_lower or "trap" in g_lower or "urban" in g_lower:
                    expanded.update(["urbano latino", "trap latino", "reggaeton"])
                if "metal" in g_lower:
                    expanded.update(["heavy metal", "thrash metal", "hard rock"])
        return list(expanded)

    def record_played_track(self, title: str, target):
        player = self.get_player(target)
        if not self.sp or not title:
            return

        async def _async_lookup():
            try:
                clean_song, extracted_artist = self._clean_title_for_search(title)
                search_q = f"artist:{extracted_artist} track:{clean_song}" if extracted_artist else clean_song
                res = self.sp.search(q=search_q, type='track', limit=1)
                if res and res.get('tracks', {}).get('items'):
                    track_data = res['tracks']['items'][0]
                    artist_id = track_data['artists'][0]['id']
                    spotify_id = track_data['id']
                    popularity = track_data.get('popularity', 0)

                    if not player.recent_artist_ids or player.recent_artist_ids[-1] != artist_id:
                        if artist_id in player.recent_artist_ids:
                            player.recent_artist_ids.remove(artist_id)
                        player.recent_artist_ids.append(artist_id)
                        if len(player.recent_artist_ids) > 5:
                            player.recent_artist_ids.pop(0)
                        print(f"📻 [RADIO PROFILE] Artista registrado para guild {player.guild_id}: {track_data['artists'][0]['name']} (Total: {len(player.recent_artist_ids)})", flush=True)

                    try:
                        artist_info = self.sp.artist(artist_id)
                        genres_list = artist_info.get('genres', [])
                        genres_str = ",".join(genres_list) if genres_list else ""
                        
                        from database import update_song_features
                        update_song_features(
                            title=title,
                            spotify_id=spotify_id,
                            genres=genres_str,
                            popularity=popularity
                        )
                    except Exception as meta_err:
                        print(f"⚠️ Error al guardar metadatos de telemetría de recomendación en BD: {meta_err}", flush=True)
            except Exception as e:
                print(f"⚠️ Error registrando artista reciente para radio: {e}", flush=True)

        self.bot.loop.create_task(_async_lookup())

    @commands.command(name="join", aliases=["connect", "conectar", "unir", "j"])
    async def join(self, ctx):
        """Conecta el bot al canal de voz actual y reanuda la cola de canciones si existe."""
        if not ctx.author.voice:
            await ctx.send("❌ ¡Debes estar en un canal de voz para traer al bot!")
            return

        vc = await self.connect_to_voice(ctx)
        if vc:
            player = self.get_player(ctx)
            if player.song_queue and not player.current_song and not vc.is_playing():
                await ctx.send(f"🔊 Conectado a **{ctx.author.voice.channel.name}**. Reanudando reproducción de la cola...")
                await self.play_next(ctx)
            else:
                await ctx.send(f"🔊 Conectado a **{ctx.author.voice.channel.name}**.")

    @commands.command(name="p", aliases=["play"])
    async def play(self, ctx, *, query: str):
        """Reproduce una canción o playlist de YouTube, YouTube Music, Spotify o SoundCloud."""
        if getattr(self.bot, 'is_draining', False):
            await ctx.send("⚠️ El bot está aplicando una actualización. Se reiniciará inmediatamente al terminar la canción actual.")
            return

        vc = await self.connect_to_voice(ctx)
        if not vc:
            return

        q = query.strip()
        import re
        spotify_match = re.search(r'(track|playlist|album|artist)[/:]([a-zA-Z0-9]+)', q)
        if "spotify.com" in q or "spotify:" in q:
            if spotify_match:
                stype, sid = spotify_match.group(1), spotify_match.group(2)
                if stype == "track":
                    await self.add_from_spotify(ctx, q, track_id=sid)
                elif stype == "playlist":
                    await self.add_playlist_from_spotify(ctx, q, playlist_id=sid)
                elif stype == "album":
                    await self.add_album_from_spotify(ctx, q, album_id=sid)
                elif stype == "artist":
                    await self.add_artist_from_spotify(ctx, q, artist_id=sid)
                else:
                    await self.add_from_spotify(ctx, q)
            else:
                await self.add_from_spotify(ctx, q)
        elif "youtube.com" in q or "youtu.be" in q or "music.youtube.com" in q:
            # Si el enlace es de un video específico (watch?v=...) y trae un Mix automático de YouTube (list=RD.../UL.../TL...), limpiar &list= para reproducir solo el tema individual
            if "watch?v=" in q and ("list=RD" in q or "list=UL" in q or "list=TL" in q):
                clean_url = re.sub(r'&list=[^&]+', '', q)
                await self.add_from_youtube(ctx, clean_url, origin=f"🎵 Pedida por {ctx.author.name}")
            elif "list=PL" in q or "list=OL" in q or ("playlist" in q and "watch?v=" not in q):
                # Playlists reales de usuario/álbum
                await self.add_playlist_from_youtube(ctx, q)
            elif "list=" in q and "watch?v=" not in q:
                await self.add_playlist_from_youtube(ctx, q)
            else:
                await self.add_from_youtube(ctx, q, origin=f"🎵 Pedida por {ctx.author.name}")
        else:
            await self.add_from_youtube(ctx, q, origin=f"🎵 Pedida por {ctx.author.name}")

    @commands.command(name="s", aliases=["skip"])
    async def skip(self, ctx):
        """Salta la canción actual."""
        player = self.get_player(ctx)
        if player.voice_client and player.voice_client.is_playing():
            player.current_song_skipped = True
            player.voice_client.stop()
            await ctx.send("⏭️ Canción saltada.")
        else:
            await ctx.send("⚠️ No hay ninguna canción reproduciéndose.")

    @commands.command(name="np", aliases=["nowplaying"])
    async def nowplaying(self, ctx):
        """Muestra la canción reproduciéndose actualmente y su progreso de tiempo."""
        player = self.get_player(ctx)
        if not player.current_song:
            await ctx.send("⚠️ No hay ninguna canción en reproducción.")
            return

        import time
        start_time = getattr(player, 'current_song_start_time', None)
        elapsed_sec = int(time.time() - start_time) if start_time else 0
        duration_sec = player.current_song.get('duration', 0)
        
        elapsed_str = self.format_duration(elapsed_sec)
        total_str = self.format_duration(duration_sec)

        if duration_sec > 0:
            percent = min(1.0, max(0.0, elapsed_sec / duration_sec))
            bar_len = 12
            filled = int(bar_len * percent)
            progress_bar = f"`{'▬' * filled}🔘{'▬' * (bar_len - filled)}`"
            time_display = f"`{elapsed_str} / {total_str}`\n{progress_bar}"
        else:
            time_display = f"`{elapsed_str}` 🔴 EN VIVO"

        embed = discord.Embed(
            title="🎧 Sonando Ahora",
            description=f"**[{player.current_song.get('title', 'Desconocido')}]({player.current_song.get('url', 'https://www.youtube.com')})**",
            color=discord.Color.green()
        )
        embed.add_field(name="👤 Artista", value=f"`{player.current_song.get('uploader', 'Artista')}`", inline=True)
        embed.add_field(name="⏱️ Progreso", value=time_display, inline=True)
        embed.add_field(name="🧬 Origen", value=f"`{player.current_song.get('origin', '🎵 Solicitada')}`", inline=False)
        
        if player.current_song.get('thumbnail') and player.current_song['thumbnail'].startswith("http"):
            embed.set_thumbnail(url=player.current_song['thumbnail'])

        await ctx.send(embed=embed)

    @commands.command()
    async def pause(self, ctx):
        """Pausa la canción actual."""
        player = self.get_player(ctx)
        if player.voice_client and player.voice_client.is_playing():
            player.voice_client.pause()
            await ctx.send("⏸️ Reproducción pausada.")

    @commands.command(name="resume", aliases=["r", "reanudar"])
    async def resume(self, ctx):
        """Reanuda la reproducción pausada o inicia la cola de canciones en espera."""
        player = self.get_player(ctx)
        if player.voice_client and player.voice_client.is_paused():
            player.voice_client.resume()
            await ctx.send("▶️ Reproducción reanudada.")
        elif player.song_queue and (not player.voice_client or not player.voice_client.is_playing()):
            vc = await self.connect_to_voice(ctx)
            if vc and not player.current_song:
                await ctx.send("▶️ Reanudando la reproducción de la cola de canciones...")
                await self.play_next(ctx)
        else:
            await ctx.send("⚠️ No hay ninguna canción pausada ni canciones pendientes en cola.")

    @commands.command(name="stop", aliases=["disconnect", "leave", "exit", "dc"])
    async def stop(self, ctx):
        """Detiene la música, desconecta del canal y guarda la cola si la persistencia está activa."""
        player = self.get_player(ctx)
        from sznUtils import save_guild_queue, is_guild_persist_enabled
        if is_guild_persist_enabled(ctx.guild.id):
            full_queue = []
            if player.current_song:
                full_queue.append(player.current_song)
            full_queue.extend(player.song_queue)
            if full_queue:
                save_guild_queue(ctx.guild.id, full_queue)

        player.song_queue.clear()
        player.radio_mode = False
        player.current_song = None  # limpiar ANTES de disconnect para que on_voice_state_update no sobreescriba la cola ya guardada
        cleanup_cache()
        if player.voice_client:
            player.voice_client.stop()
            await player.voice_client.disconnect()
            player.voice_client = None
        await ctx.send("🛑 Reproducción detenida y bot desconectado (la cola fue guardada en BD si la persistencia está activa).")

    @commands.command(name="clear", aliases=["clean", "cq"])
    async def clear(self, ctx):
        """Limpia la cola de canciones y la base de datos sin detener la canción actual."""
        player = self.get_player(ctx)
        if not player.song_queue:
            await ctx.send("⚠️ La cola ya está vacía.")
            return

        for song in player.song_queue:
            cleanup_cache(song)

        from sznUtils import save_guild_queue
        save_guild_queue(ctx.guild.id, [])
        player.song_queue.clear()
        self.schedule_queue_optimizations(ctx)
        await ctx.send("🧹 Cola de canciones vaciada completamente (canción actual continúa).")

    @commands.command()
    async def shuffle(self, ctx):
        """Mezcla la cola de canciones aleatoriamente."""
        player = self.get_player(ctx)
        if not player.song_queue:
            await ctx.send("⚠️ La cola está vacía.")
            return
        random.shuffle(player.song_queue)
        self.schedule_queue_optimizations(ctx)
        await ctx.send("🔀 Cola de canciones mezclada aleatoriamente.")

    @commands.command()
    async def remove(self, ctx, index: int):
        """Elimina una canción específica de la cola según su número."""
        player = self.get_player(ctx)
        if index < 1 or index > len(player.song_queue):
            await ctx.send(f"❌ Número fuera de rango. La cola tiene {len(player.song_queue)} canciones.")
            return
        removed = player.song_queue.pop(index - 1)
        cleanup_cache(removed)
        self.schedule_queue_optimizations(ctx)
        await ctx.send(f"🗑️ Canción eliminada de la cola: **{removed['title']}**")

    @commands.command()
    async def move(self, ctx, old_index: int, new_index: int):
        """Mueve una canción de una posición a otra en la cola."""
        player = self.get_player(ctx)
        if old_index < 1 or old_index > len(player.song_queue) or new_index < 1 or new_index > len(player.song_queue):
            await ctx.send("❌ Índices fuera de rango.")
            return
        song = player.song_queue.pop(old_index - 1)
        player.song_queue.insert(new_index - 1, song)
        self.schedule_queue_optimizations(ctx)
        await ctx.send(f"↕️ **{song['title']}** movida de la posición {old_index} a la {new_index}.")

    @commands.command()
    async def search(self, ctx, *, query: str):
        """Busca y permite seleccionar canciones usando fuzzy matching o búsqueda de Piped."""
        try:
            info = await extract_info(query)
            await self.add_song_dict(ctx, info, origin=f"🔍 Búsqueda por {ctx.author.name}")
        except Exception as e:
            await ctx.send(f"❌ No se encontraron resultados para: '{query}'")

    @tasks.loop(hours=6)
    async def recsys_training_loop(self):
        """Entrena el modelo de recomendación automáticamente cada 6 horas."""
        try:
            print("🔄 [RecSys] Iniciando entrenamiento automático...", flush=True)
            from recsys.train import main as train_recsys
            await asyncio.to_thread(train_recsys)
            self.recsys_engine.load(force=True)
            print("✅ [RecSys] Entrenamiento completado y motor recargado.", flush=True)
        except Exception as e:
            print(f"⚠️ [RecSys] Error en entrenamiento automático: {e}", flush=True)

    @recsys_training_loop.before_loop
    async def before_recsys_training(self):
        """Espera a que el bot esté listo y entrena inmediatamente si no hay artefactos pre-existentes."""
        await self.bot.wait_until_ready()
        if self.recsys_engine and self.recsys_engine.loaded:
            await asyncio.sleep(300)  # 5 min de gracia solo si ya había modelo cargado en disco

    @tasks.loop(seconds=60)
    async def inactivity_check(self):
        for guild_id, player in list(self.players.items()):
            if getattr(player, 'is_loading_song', False) or getattr(player, 'current_song', None) is not None:
                continue
            vc = player.voice_client
            if vc and not vc.is_playing() and not getattr(vc, 'is_paused', lambda: False)() and not player.song_queue:
                await vc.disconnect()
                player.voice_client = None
                player.current_song = None
                player.radio_mode = False
                cleanup_cache()
                print(f"✅ Desconectado por inactividad en guild {guild_id}.", flush=True)
                if player.last_ctx:
                    try:
                        await player.last_ctx.send("💤 Me he desconectado del canal de voz por inactividad (cola vacía durante 60 segundos).")
                    except Exception:
                        pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # 1. El bot fue desconectado directamente
        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                if getattr(self.bot, 'is_draining', False):
                    return

                player = self.get_player(member.guild)
                from sznUtils import save_guild_queue, is_guild_persist_enabled
                if is_guild_persist_enabled(member.guild.id):
                    full_queue = []
                    if player.current_song:
                        full_queue.append(player.current_song)
                    full_queue.extend(player.song_queue)
                    if full_queue:
                        save_guild_queue(member.guild.id, full_queue)

                player.song_queue.clear()
                player.radio_mode = False
                cleanup_cache()
                player.voice_client = None
                player.current_song = None
                
                if player.last_ctx:
                    try:
                        await player.last_ctx.send("🔌 Me he desconectado del canal de voz (desconexión manual o externa).")
                    except Exception:
                        pass

        # 2. Un usuario humano salió de un canal de voz
        elif before.channel and (not after.channel or after.channel.id != before.channel.id):
            player = self.get_player(member.guild)
            vc = player.voice_client
            if vc and vc.channel and vc.channel.id == before.channel.id:
                non_bots = [m for m in vc.channel.members if not m.bot]
                if len(non_bots) == 0:
                    async def _disconnect_if_still_alone():
                        await asyncio.sleep(30)
                        if player.voice_client and player.voice_client.channel and len([m for m in player.voice_client.channel.members if not m.bot]) == 0:
                            from sznUtils import save_guild_queue, is_guild_persist_enabled
                            if is_guild_persist_enabled(member.guild.id):
                                full_queue = []
                                if player.current_song:
                                    full_queue.append(player.current_song)
                                full_queue.extend(player.song_queue)
                                if full_queue:
                                    save_guild_queue(member.guild.id, full_queue)

                            player.song_queue.clear()
                            player.radio_mode = False
                            player.current_song = None
                            cleanup_cache()
                            try:
                                if player.voice_client:
                                    player.voice_client.stop()
                                    await player.voice_client.disconnect()
                            except Exception:
                                pass
                            player.voice_client = None
                            
                            if player.last_ctx:
                                try:
                                    await player.last_ctx.send("👤 Me he desconectado automáticamente al quedarme solo en el canal. (La cola fue guardada en BD).")
                                except Exception:
                                    pass

                    self.bot.loop.create_task(_disconnect_if_still_alone())

    @commands.command()
    async def radio(self, ctx, *, arg: str = "on"):
        """Activa o desactiva el modo radio automática."""
        player = self.get_player(ctx)
        if arg.lower() in ("off", "stop", "desactivar"):
            player.radio_mode = False
            player.radio_seed_id = None
            await ctx.send("🛑 Modo radio desactivado.")
            return

        if not player.current_song and not player.song_queue:
            await ctx.send("⚠️ Debe haber una canción reproduciéndose o en cola para iniciar el modo radio.")
            return

        if getattr(player, 'radio_mode', False):
            await ctx.send("📻 El modo radio ya está activo.")
            return

        player.radio_mode = True
        if player.current_song:
            player.last_played_title = player.current_song.get('title')

        await ctx.send("📻 **Modo radio activado**. Se generarán 5 recomendaciones a la cola.")
        await self.expand_radio_queue(ctx)

        if player.voice_client and not player.voice_client.is_playing() and not player.voice_client.is_paused():
            await self.play_next(ctx)

    async def _fill_queue_from_recsys(self, ctx, player):
        """Llena la cola usando el motor de recomendación ML cuando autoplay está activo."""
        try:
            # Recargar artefactos si fueron actualizados
            self.recsys_engine.reload_if_updated()
            
            # Obtener IDs de usuarios en el canal de voz
            user_ids = []
            if ctx.author.voice and ctx.author.voice.channel:
                user_ids = [str(m.id) for m in ctx.author.voice.channel.members if not m.bot]
            
            recommendations = []
            
            # Intentar con el motor ML primero
            if self.recsys_engine.loaded:
                recommendations = await asyncio.to_thread(
                    self.recsys_engine.get_autoplay_recommendations,
                    current_title=player.last_played_title,
                    user_ids=user_ids if user_ids else None,
                    recent_titles=list(player.radio_history),
                    temperature=getattr(player, 'radio_temperature', 0.75),
                    n=5
                )
            
            # Fallback: recomendaciones basadas en historial del servidor (cold-start)
            if not recommendations:
                from database import get_cold_start_recommendations
                guild_id = str(player.guild_id) if player.guild_id else None
                recommendations = await asyncio.to_thread(
                    get_cold_start_recommendations,
                    guild_id=guild_id,
                    exclude_titles=list(player.radio_history),
                    limit=5
                )
            
            if not recommendations:
                return
            
            added_count = 0
            for rec in recommendations:
                title = rec.get('title', '')
                if not title:
                    continue
                
                # Verificar unicidad contra la cola actual
                if any(title.lower() == s.get('title', '').lower() for s in player.song_queue):
                    continue
                
                song_dict = {
                    'title': title,
                    'url': None,
                    'duration': 0,
                    'uploader': rec.get('artist', ''),
                    'origin': '🤖 Autoplay ML',
                    'user_id': None,
                    'username': 'RecSys',
                    'guild_id': str(player.guild_id),
                    'id': None,
                    'cache_path': None
                }
                player.song_queue.append(song_dict)
                player.radio_history.append(title.lower())
                if len(player.radio_history) > 30:
                    player.radio_history.pop(0)
                added_count += 1
            
            if added_count > 0:
                source_info = recommendations[0].get('source', 'ml')
                print(f"🤖 [RecSys] {added_count} canciones añadidas a la cola via {source_info}", flush=True)
                
        except Exception as e:
            print(f"⚠️ [RecSys] Error en autoplay ML: {e}", flush=True)

    @commands.command()
    async def autoplay(self, ctx, *, arg: str = "on"):
        """Activa o desactiva el autoplay inteligente basado en ML."""
        player = self.get_player(ctx)
        if arg.lower() in ("off", "stop", "desactivar"):
            player.autoplay_mode = False
            await ctx.send("🛑 Autoplay ML desactivado.")
            return
        
        player.autoplay_mode = True
        
        if not self.recsys_engine.loaded:
            await ctx.send("🤖 **Autoplay ML activado** (motor de recomendación no entrenado aún, se usará radio de Spotify como fallback).")
        else:
            stats = self.recsys_engine.stats
            await ctx.send(
                f"🤖 **Autoplay ML activado** — Motor cargado con "
                f"{stats['n_songs']} canciones y {stats['n_users']} usuarios."
            )
        
        # Si la cola está vacía y no hay nada sonando, llenar inmediatamente
        if not player.song_queue and not (player.voice_client and player.voice_client.is_playing()):
            await self._fill_queue_from_recsys(ctx, player)
            if player.song_queue and player.voice_client and not player.voice_client.is_playing():
                await self.play_next(ctx)

    @commands.command()
    async def reloadrecsys(self, ctx):
        """Recarga los artefactos del motor de recomendación ML desde disco."""
        msg = await ctx.send("🔄 Recargando motor de recomendación...")
        success = await asyncio.to_thread(self.recsys_engine.load, True)
        if success:
            stats = self.recsys_engine.stats
            await msg.edit(content=(
                f"✅ Motor recargado: {stats['n_songs']} canciones, "
                f"{stats['n_users']} usuarios, "
                f"ALS={'✅' if stats['has_als'] else '❌'}, "
                f"Item2Vec={'✅' if stats['has_item2vec'] else '❌'}"
            ))
        else:
            await msg.edit(content="❌ No se pudieron cargar los artefactos. Ejecuta el entrenamiento primero.")

    @commands.command()
    async def preload(self, ctx, url: str = None, target: discord.User = None):
        """Precarga una playlist de Spotify o YouTube directamente en la BBDD asignada a ti o a un usuario mencionado."""
        if not url:
            await ctx.send("⚠️ Por favor proporciona un enlace de playlist de Spotify o YouTube/YouTube Music. Ejemplo: `td?preload <URL> [@usuario]`")
            return

        target_user = target or ctx.author
        target_user_id = str(target_user.id)
        target_username = target_user.name

        msg = await ctx.send(f"📥 Extrayendo canciones de la playlist/álbum para precargar como gustos de **@{target_username}**...")
        tracks_to_preload = []

        try:
            url_clean = url.strip()
            
            # 1. Playlist de Spotify
            if "spotify.com/playlist" in url_clean:
                if not self.sp:
                    await msg.edit(content="❌ La API de Spotify no está configurada.")
                    return
                playlist_id = url_clean.split("/")[-1].split("?")[0]
                all_items = []
                results = await asyncio.to_thread(self.sp.playlist_tracks, playlist_id, limit=100)
                while results:
                    all_items.extend(results.get('items', []))
                    results = await asyncio.to_thread(self.sp.next, results) if results.get('next') else None

                for item in all_items:
                    t = item.get('track')
                    if t and t.get('name'):
                        artist_name = t['artists'][0]['name'] if t.get('artists') else ''
                        tracks_to_preload.append({
                            'title': f"{t['name']} - {artist_name}" if artist_name else t['name'],
                            'artist': artist_name,
                            'duration': int(t.get('duration_ms', 0) / 1000)
                        })

            # 2. Álbum de Spotify
            elif "spotify.com/album" in url_clean:
                if not self.sp:
                    await msg.edit(content="❌ La API de Spotify no está configurada.")
                    return
                album_id = url_clean.split("/")[-1].split("?")[0]
                album = await asyncio.to_thread(self.sp.album, album_id)
                tracks = album.get('tracks', {}).get('items', [])
                for t in tracks:
                    if t and t.get('name'):
                        artist_name = t['artists'][0]['name'] if t.get('artists') else ''
                        tracks_to_preload.append({
                            'title': f"{t['name']} - {artist_name}" if artist_name else t['name'],
                            'artist': artist_name,
                            'duration': int(t.get('duration_ms', 0) / 1000)
                        })

            # 3. YouTube / YouTube Music
            elif "youtube.com" in url_clean or "youtu.be" in url_clean:
                from sznUtils import extract_playlist_metadata
                yt_tracks = await asyncio.to_thread(extract_playlist_metadata, url_clean)
                for t in yt_tracks:
                    tracks_to_preload.append({
                        'title': t.get('title'),
                        'artist': t.get('uploader'),
                        'duration': t.get('duration', 0),
                        'url': t.get('url')
                    })

            else:
                await msg.edit(content="❌ Enlace no reconocido. Debe ser una playlist/álbum de Spotify o YouTube.")
                return

            if not tracks_to_preload:
                await msg.edit(content="📭 No se encontraron canciones válidas en la playlist.")
                return

            await msg.edit(content=f"⚙️ Guardando **{len(tracks_to_preload)}** canciones en la BBDD asignadas a **@{target_username}**...")
            
            from database import bulk_preload_tracks
            result = await asyncio.to_thread(
                bulk_preload_tracks,
                tracks=tracks_to_preload,
                user_id=target_user_id,
                username=target_username,
                guild_id=str(ctx.guild.id) if ctx.guild else None
            )

            # Reentrenar RecSys en segundo plano inmediatamente
            await msg.edit(content=f"🧠 Reentrenando motor ML con **{result['total']}** temas precargados para **@{target_username}**...")
            from recsys.train import main as train_recsys
            await asyncio.to_thread(train_recsys)
            self.recsys_engine.load(force=True)

            stats = self.recsys_engine.stats
            await msg.edit(content=(
                f"✅ **Precarga completada exitosamente para @{target_username}**!\n"
                f"📊 **Resultados**:\n"
                f"• Canciones procesadas: **{result['total']}**\n"
                f"• Nuevas en BBDD: **{result['new_songs']}**\n"
                f"• Likes añadidos a @{target_username}: **{result['likes_added']}**\n"
                f"🚀 **Motor ML actualizado**: {stats['n_songs']} canciones y {stats['n_users']} usuarios listos para Autoplay."
            ))

        except Exception as e:
            print(f"❌ Error en preload: {e}", flush=True)
            await msg.edit(content=f"❌ Error durante la precarga: {e}")

async def setup(bot):
    await bot.add_cog(MusicCore(bot))
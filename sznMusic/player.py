import asyncio
import os
import shutil
import tempfile
import time
import discord
from rapidfuzz import process, fuzz

from database import add_or_update_song
from sznUtils import extract_info, RateLimitError, ForbiddenBlockError

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
            "extractor_args": {"youtube": {"player_client": ["web", "ios", "mweb"]}}
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
            
            try:
                from sznUtils import extract_local_audio_features
                from database import update_song_audio_features
                feats = await asyncio.to_thread(extract_local_audio_features, final_cache_path)
                if feats:
                    await asyncio.to_thread(update_song_audio_features, song_info.get('title'), feats)
                    print(f"🎼 Métricas acústicas reales extraídas ({song_info.get('title')}): BPM={feats.get('tempo')}, Energy={feats.get('energy')}, Brightness={feats.get('valence')}", flush=True)
            except Exception as feat_err:
                print(f"⚠️ Error al analizar métricas de audio: {feat_err}", flush=True)

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

def cleanup_old_cache(max_age_seconds: int = 3600):
    """Elimina cualquier archivo .webm o .tmp en /tmp con más de max_age_seconds de antigüedad (por defecto 1 hora)."""
    try:
        temp_dir = tempfile.gettempdir()
        now = time.time()
        removed_count = 0
        for fname in os.listdir(temp_dir):
            if (fname.startswith("cache_") or fname.endswith(".webm") or fname.endswith(".tmp")) and (fname.endswith(".webm") or fname.endswith(".tmp")):
                fpath = os.path.join(temp_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        file_age = now - os.path.getmtime(fpath)
                        if file_age > max_age_seconds:
                            os.remove(fpath)
                            removed_count += 1
                    except Exception:
                        pass
        if removed_count > 0:
            print(f"🧹 [CACHE DISK] Limpieza de disco: eliminados {removed_count} archivos de caché antiguos (>1h).", flush=True)
    except Exception as e:
        print(f"⚠️ Error durante limpieza periódica de caché antiguo: {e}", flush=True)

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

def _clean_title_for_search(title: str) -> tuple[str, str | None]:
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


class MusicPlayerMixin:
    """Métodos de gestión de reproductor, precarga y extracción de audio."""
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
                await asyncio.sleep(30)
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
        except RateLimitError as e:
            print(f"🚫 [RATE LIMIT] YouTube HTTP 429 ({query}): {e}", flush=True)
            if notify and ctx:
                await ctx.send("🚫 **Alerta de Extracción (HTTP 429)**: YouTube ha limitado las peticiones de la IP por exceso de tráfico.")
        except ForbiddenBlockError as e:
            print(f"🚫 [BOT BLOCK / 403] YouTube HTTP 403 ({query}): {e}", flush=True)
            if notify and ctx:
                await ctx.send("🚫 **Alerta de Extracción (HTTP 403 / Bot Block)**: YouTube rechazó la solicitud por sospecha de bot.")
        except Exception as e:
            print(f"❌ Error interno en la búsqueda/extracción ({query}): {e}", flush=True)
            if notify and ctx:
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
                except RateLimitError as e:
                    print(f"🚫 [RATE LIMIT] Error al resolver tema de cola ({player.current_song.get('title')}): {e}", flush=True)
                    player.current_song = None
                    self.bot.loop.create_task(self.play_next(ctx))
                    return
                except ForbiddenBlockError as e:
                    print(f"🚫 [BOT BLOCK] Error al resolver tema de cola ({player.current_song.get('title')}): {e}", flush=True)
                    player.current_song = None
                    self.bot.loop.create_task(self.play_next(ctx))
                    return
                except Exception as e:
                    print(f"⚠️ Error al resolver tema de cola ({player.current_song.get('title')}): {e}", flush=True)
                    player.current_song = None
                    self.bot.loop.create_task(self.play_next(ctx))
                    return

            target_path = player.current_song.get('cache_path')
            if not target_path or not os.path.exists(target_path):
                target_path = player.current_song.get('url')

            if target_path and target_path.startswith("http"):
                headers_dict = player.current_song.get('http_headers') or {}
                user_agent = headers_dict.get('User-Agent') or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                header_lines = "".join(f"{k}: {v}\r\n" for k, v in headers_dict.items() if k.lower() != 'user-agent')
                if not header_lines:
                    header_lines = "Referer: https://www.youtube.com/\r\n"
                before_opts = f'-probesize 64k -analyzeduration 0 -user_agent "{user_agent}" -headers "{header_lines}" -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 3'
            else:
                before_opts = '-probesize 64k -analyzeduration 0'
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

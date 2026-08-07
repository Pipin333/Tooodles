import asyncio
import os
import random
import time
import discord
from discord.ext import commands, tasks

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from recsys.engine import RecSysEngine

from .player import (
    GuildPlayer,
    prefetch_chunk_throttled,
    cleanup_cache,
    cleanup_old_cache,
    fuzzy_find_songs,
    _clean_title_for_search,
    MusicPlayerMixin
)
from .radio import MusicRadioMixin, GENRE_EXPANSION

SPOTIFY_CLIENT_ID = os.getenv('client_id')
SPOTIFY_CLIENT_SECRET = os.getenv('client_secret')


class MusicCore(commands.Cog, MusicPlayerMixin, MusicRadioMixin):
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
            self.cache_cleanup_loop.start()
        except Exception as e:
            print(f"❌ Error al iniciar cache_cleanup_loop: {e}")

        try:
            self.recsys_training_loop.start()
        except Exception as e:
            print(f"⚠️ [RecSys] Error al iniciar loop de entrenamiento: {e}")

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
            if "watch?v=" in q and ("list=RD" in q or "list=UL" in q or "list=TL" in q):
                clean_url = re.sub(r'&list=[^&]+', '', q)
                await self.add_from_youtube(ctx, clean_url, origin=f"🎵 Pedida por {ctx.author.name}")
            elif "list=PL" in q or "list=OL" in q or ("playlist" in q and "watch?v=" not in q):
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

        ui = self.bot.get_cog("MusicUI")
        if ui:
            await ui.notify_now_playing(ctx, player.current_song)
            return

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

        origin = player.current_song.get('origin', '🎵 Solicitada')
        is_radio = "radio" in origin.lower()
        embed_color = 0x1db954 if "spotify" in origin.lower() else (0x8a2be2 if is_radio else 0x7d5fff)

        embed = discord.Embed(
            title="🎶 Ahora Reproduciendo" if not is_radio else "📻 Radio Automática",
            description=f"**[{player.current_song.get('title', 'Desconocido')}]({player.current_song.get('url', 'https://www.youtube.com')})**",
            color=embed_color
        )
        embed.add_field(name="👤 Artista", value=f"`{player.current_song.get('uploader', 'Artista')}`", inline=True)
        embed.add_field(name="⏱️ Progreso", value=time_display, inline=True)
        embed.add_field(name="🧬 Origen", value=f"`{origin}`", inline=False)
        
        if player.current_song.get('thumbnail') and player.current_song['thumbnail'].startswith("http"):
            embed.set_thumbnail(url=player.current_song['thumbnail'])

        vc_name = ctx.author.voice.channel.name if ctx.author and ctx.author.voice else "Canal de voz"
        vibe_size = len(getattr(player, 'recent_artist_ids', []))
        embed.set_footer(text=f"🔊 VC: {vc_name}  •  🧬 Perfil Radio: {vibe_size}/5 artistas")

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
        player.current_song = None
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
            from sznUtils import extract_info, RateLimitError, ForbiddenBlockError
            info = await extract_info(query)
            await self.add_song_dict(ctx, info, origin=f"🔍 Búsqueda por {ctx.author.name}")
        except RateLimitError as e:
            print(f"🚫 [RATE LIMIT] {e}", flush=True)
            await ctx.send("🚫 **Alerta de Extracción (HTTP 429)**: YouTube ha limitado las peticiones de la IP por exceso de tráfico.")
        except ForbiddenBlockError as e:
            print(f"🚫 [BOT BLOCK] {e}", flush=True)
            await ctx.send("🚫 **Alerta de Extracción (HTTP 403 / Bot Block)**: YouTube rechazó la petición de búsqueda por detección de bot.")
        except Exception as e:
            await ctx.send(f"❌ No se encontraron resultados para: '{query}'")

    @tasks.loop(minutes=30)
    async def cache_cleanup_loop(self):
        """Elimina automáticamente del disco cualquier archivo .webm o .tmp mayor a 1 hora de antigüedad."""
        try:
            await asyncio.to_thread(cleanup_old_cache, max_age_seconds=3600)
        except Exception as e:
            print(f"⚠️ Error en limpieza automática de caché antiguo: {e}", flush=True)

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
            await asyncio.sleep(300)

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

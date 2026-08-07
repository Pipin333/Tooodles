import asyncio
import discord
from discord.ext import commands
from rapidfuzz import process
from database import (
    get_top_songs,
    get_recent_history,
    add_or_update_song,
    preload_top_songs_cache,
    get_db_session,
    Song,
    UserLike,
    UserDislike,
    AppConfig,
    PlayLog,
    log_dislike_event,
    remove_dislike
)

class MusicDB(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        preload_top_songs_cache()
        self.last_played = []  # historial reciente en memoria (fallback)

    def log_song(self, title):
        self.last_played.insert(0, title)
        if len(self.last_played) > 20:
            self.last_played.pop()

        try:
            with get_db_session() as session:
                song = session.query(Song).filter_by(title=title).first()
                if song:
                    song.played_count = (song.played_count or 0) + 1
                else:
                    new_song = Song(title=title, played_count=1)
                    session.add(new_song)
        except Exception as e:
            print(f"⚠️ Error al registrar reproducción de canción: {e}")

    def find_similar_song(self, query, threshold=90):
        try:
            with get_db_session() as session:
                songs = session.query(Song).all()
                choices = {song.title: song for song in songs if song.title}
                if not choices:
                    return None
                match = process.extractOne(query, choices.keys())
                if match and match[1] >= threshold:
                    return choices[match[0]]
        except Exception as e:
            print(f"⚠️ Error en búsqueda difusa de canciones: {e}")
        return None

    def get_liked_songs_by_user(self, user_id):
        try:
            with get_db_session() as session:
                likes = session.query(UserLike).filter_by(user_id=str(user_id)).all()
                if not likes:
                    return []
                song_ids = [like.song_id for like in likes]
                return session.query(Song).filter(Song.id.in_(song_ids)).all()
        except Exception as e:
            print(f"⚠️ Error al obtener canciones favoritas: {e}")
            return []

    def get_liked_songs_by_users(self, user_ids):
        try:
            str_user_ids = [str(uid) for uid in user_ids]
            with get_db_session() as session:
                likes = session.query(UserLike).filter(UserLike.user_id.in_(str_user_ids)).all()
                if not likes:
                    return []
                song_ids = {like.song_id for like in likes}
                return session.query(Song).filter(Song.id.in_(song_ids)).all()
        except Exception as e:
            print(f"⚠️ Error al obtener canciones favoritas del grupo: {e}")
            return []

    def like_song(self, user_id, song_title):
        try:
            with get_db_session() as session:
                song = session.query(Song).filter_by(title=song_title).first()
                if not song:
                    return False
                existing = session.query(UserLike).filter_by(user_id=str(user_id), song_id=song.id).first()
                if not existing:
                    like = UserLike(user_id=str(user_id), song_id=song.id)
                    session.add(like)
                    return True
                return False
        except Exception as e:
            print(f"⚠️ Error al dar me gusta: {e}")
            return False

    def unlike_song(self, user_id, song_title):
        try:
            with get_db_session() as session:
                song = session.query(Song).filter_by(title=song_title).first()
                if not song:
                    return False
                existing = session.query(UserLike).filter_by(user_id=str(user_id), song_id=song.id).first()
                if existing:
                    session.delete(existing)
                    return True
                return False
        except Exception as e:
            print(f"⚠️ Error al eliminar me gusta: {e}")
            return False

    @commands.command()
    async def like(self, ctx):
        """Le da me gusta a la canción que se está reproduciendo actualmente."""
        core = self.bot.get_cog("MusicCore")
        if not core or not core.current_song:
            await ctx.send("⚠️ No hay ninguna canción en reproducción.")
            return
        added = await asyncio.to_thread(self.like_song, str(ctx.author.id), core.current_song['title'])
        if added:
            await ctx.send(f"❤️ Canción guardada en tus favoritas: **{core.current_song['title']}**")
        else:
            await ctx.send("✅ Esta canción ya está en tus favoritas.")

    @commands.command()
    async def unlike(self, ctx):
        """Elimina la canción actual de tus favoritas."""
        core = self.bot.get_cog("MusicCore")
        if not core or not core.current_song:
            await ctx.send("⚠️ No hay ninguna canción en reproducción.")
            return
        removed = await asyncio.to_thread(self.unlike_song, str(ctx.author.id), core.current_song['title'])
        if removed:
            await ctx.send(f"❌ Canción eliminada de tus favoritas: **{core.current_song['title']}**")
        else:
            await ctx.send("ℹ️ Esta canción no estaba en tus favoritas.")

    @commands.command()
    async def dislike(self, ctx):
        """Marca la canción actual como no gustada (feedback negativo para recomendaciones)."""
        core = self.bot.get_cog("MusicCore")
        if not core or not core.current_song:
            await ctx.send("⚠️ No hay ninguna canción en reproducción.")
            return
        added = await asyncio.to_thread(log_dislike_event, str(ctx.author.id), core.current_song['title'])
        if added:
            await ctx.send(f"👎 Canción marcada como no gustada: **{core.current_song['title']}**")
        else:
            await ctx.send("ℹ️ Ya habías marcado esta canción como no gustada.")

    @commands.command()
    async def undislike(self, ctx):
        """Elimina la marca de no me gusta de la canción actual."""
        core = self.bot.get_cog("MusicCore")
        if not core or not core.current_song:
            await ctx.send("⚠️ No hay ninguna canción en reproducción.")
            return
        removed = await asyncio.to_thread(remove_dislike, str(ctx.author.id), core.current_song['title'])
        if removed:
            await ctx.send(f"✅ Se eliminó la marca de no gustada: **{core.current_song['title']}**")
        else:
            await ctx.send("ℹ️ Esta canción no estaba marcada como no gustada.")

    @commands.command()
    async def liked(self, ctx):
        """Muestra tus canciones favoritas."""
        songs = await asyncio.to_thread(self.get_liked_songs_by_user, str(ctx.author.id))
        if not songs:
            await ctx.send("📭 No tienes canciones favoritas aún. Usa `td?like` mientras suena una canción.")
            return
        description = "\n".join([f"{i}. **{song.title}**" for i, song in enumerate(songs[:10], 1)])
        embed = self.format_embed("❤️ Tus Canciones Favoritas", description)
        await ctx.send(embed=embed)

    @commands.command()
    async def favradio(self, ctx, temperatura: float = 0.75):
        """Activa modo radio grupal usando las canciones favoritas de los miembros en la llamada."""
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("⚠️ Debes estar en un canal de voz para usar este comando.")
            return

        core = self.bot.get_cog("MusicCore")
        if not core:
            await ctx.send("❌ Módulo de música no encontrado.")
            return

        vc = await core.connect_to_voice(ctx)
        if not vc:
            return

        members = ctx.author.voice.channel.members
        user_ids = [str(m.id) for m in members if not m.bot]
        liked_songs = self.get_liked_songs_by_users(user_ids)

        if not liked_songs:
            await ctx.send("📭 Ningún participante en la llamada tiene canciones favoritas. Generando radio basada en el Top del servidor...")
            guild_id = ctx.guild.id if ctx.guild else None
            top_songs = await asyncio.to_thread(get_top_songs, guild_id=guild_id, limit=10)
            if not top_songs or not core.sp:
                await ctx.send("⚠️ No se pudo generar recomendaciones.")
                return
            for title, _ in top_songs[:5]:
                results = core.sp.search(q=title, type='track', limit=1)
                if results and results.get('tracks', {}).get('items'):
                    seed_id = results['tracks']['items'][0]['id']
                    core.radio_mode = True
                    core.radio_seed_id = seed_id
                    await core.expand_radio_queue(ctx, seed_id=seed_id)
                    if not core.current_song and core.voice_client and not core.voice_client.is_playing():
                        await core.play_next(ctx)
                    break
            else:
                await ctx.send("⚠️ No se pudo generar recomendaciones basadas en el top del servidor.")
            return

        if not core.sp:
            await ctx.send("❌ La conexión con Spotify no está configurada.")
            return

        await ctx.send("🎧 **Radio Grupal Activada**: Generando recomendaciones basadas en gustos colectivos...")
        import random
        random_song = random.choice(liked_songs)
        results = core.sp.search(q=random_song.title, type='track', limit=1)
        if results and results.get('tracks', {}).get('items'):
            seed_id = results['tracks']['items'][0]['id']
            core.radio_mode = True
            core.radio_seed_id = seed_id
            await core.expand_radio_queue(ctx, seed_id=seed_id)
            if not core.current_song and core.voice_client and not core.voice_client.is_playing():
                await core.play_next(ctx)
        else:
            await ctx.send("⚠️ No se pudo generar recomendaciones basadas en canciones favoritas.")

    @commands.command(name="historial")
    async def historial(self, ctx):
        """Muestra las últimas canciones reproducidas en este servidor."""
        guild_id = ctx.guild.id if ctx.guild else None
        history_titles = await asyncio.to_thread(get_recent_history, guild_id=guild_id, limit=10)
        if not history_titles and self.last_played:
            history_titles = self.last_played[:10]

        if history_titles:
            description = "\n".join([
                f"{i + 1}. **{title}**" for i, title in enumerate(history_titles)
            ])
            embed_title = f"🎧 Últimas Canciones Reproducidas - {ctx.guild.name}" if ctx.guild else "🎧 Últimas Canciones Reproducidas"
            await ctx.send(embed=self.format_embed(embed_title, description))
        else:
            await ctx.send("📭 No hay historial reciente de reproducción en este servidor.")

    @commands.command(name="top")
    async def top(self, ctx):
        """Muestra las canciones más reproducidas en este servidor."""
        guild_id = ctx.guild.id if ctx.guild else None
        top_songs = await asyncio.to_thread(get_top_songs, guild_id=guild_id, limit=10)
        if top_songs:
            description = "\n".join([
                f"{i + 1}. **{title}** – `{count} reproducciones`"
                for i, (title, count) in enumerate(top_songs)
            ])
            embed_title = f"📈 Top Canciones - {ctx.guild.name}" if ctx.guild else "📈 Top Canciones Más Reproducidas"
            await ctx.send(embed=self.format_embed(embed_title, description))
        else:
            await ctx.send("📭 No hay estadísticas de canciones en este servidor aún.")

    def format_embed(self, title, content):
        embed = discord.Embed(
            title=title,
            description=content,
            color=0x7d5fff
        )
        embed.set_footer(text="Tooodles Music System")
        return embed

async def setup(bot):
    musicdb = MusicDB(bot)
    await bot.add_cog(musicdb)
    bot.musicdb = musicdb
    preload_top_songs_cache(limit=10)
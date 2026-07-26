import discord
from discord.ext import commands
from rapidfuzz import process
from database import (
    get_top_songs,
    add_or_update_song,
    preload_top_songs_cache,
    get_db_session,
    Song,
    UserLike,
    AppConfig
)

class MusicDB(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        preload_top_songs_cache()
        self.last_played = []  # historial reciente en memoria

    def log_song(self, title):
        self.last_played.insert(0, title)
        if len(self.last_played) > 20:
            self.last_played.pop()

        try:
            with get_db_session() as session:
                song = session.query(Song).filter_by(title=title).first()
                if song:
                    song.played_count = (song.played_count or 0) + 1
        except Exception as e:
            print(f"⚠️ Error al registrar reproducción de canción: {e}")

    def find_similar_song(self, query, threshold=90):
        try:
            with get_db_session() as session:
                songs = session.query(Song).all()
                choices = {song.title: song for song in songs}
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
                likes = session.query(UserLike).filter_by(user_id=user_id).all()
                if not likes:
                    return []
                song_ids = [like.song_id for like in likes]
                return session.query(Song).filter(Song.id.in_(song_ids)).all()
        except Exception as e:
            print(f"⚠️ Error al obtener canciones favoritas: {e}")
            return []

    def get_liked_songs_by_users(self, user_ids):
        try:
            with get_db_session() as session:
                likes = session.query(UserLike).filter(UserLike.user_id.in_(user_ids)).all()
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
                existing = session.query(UserLike).filter_by(user_id=user_id, song_id=song.id).first()
                if not existing:
                    like = UserLike(user_id=user_id, song_id=song.id)
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
                existing = session.query(UserLike).filter_by(user_id=user_id, song_id=song.id).first()
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
        added = self.like_song(str(ctx.author.id), core.current_song['title'])
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
        removed = self.unlike_song(str(ctx.author.id), core.current_song['title'])
        if removed:
            await ctx.send(f"❌ Canción eliminada de tus favoritas: **{core.current_song['title']}**")
        else:
            await ctx.send("ℹ️ Esta canción no estaba en tus favoritas.")

    @commands.command()
    async def liked(self, ctx):
        """Muestra tus canciones favoritas."""
        songs = self.get_liked_songs_by_user(str(ctx.author.id))
        if not songs:
            await ctx.send("📭 No tienes canciones favoritas aún.")
            return
        message = "**🎵 Tus canciones favoritas:**\n"
        for i, song in enumerate(songs[:10], 1):
            message += f"{i}. **{song.title}**\n"
        await ctx.send(message)

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

        members = ctx.author.voice.channel.members
        user_ids = [str(m.id) for m in members if not m.bot]
        liked_songs = self.get_liked_songs_by_users(user_ids)

        if not liked_songs:
            await ctx.send("📭 Ninguno de los usuarios en llamada tiene canciones favoritas registradas. Usando canciones populares como base.")
            top_songs = get_top_songs(10)
            if not top_songs or not core.sp:
                await ctx.send("⚠️ No se pudo generar recomendaciones.")
                return
            for title, _ in top_songs[:5]:
                results = core.sp.search(q=title, type='track', limit=1)
                if results and results.get('tracks', {}).get('items'):
                    seed_id = results['tracks']['items'][0]['id']
                    await core.expand_radio_queue(ctx, seed_id, temperatura)
                    break
            else:
                await ctx.send("⚠️ No se pudo generar recomendaciones basadas en el top global.")
            return

        if not core.sp:
            await ctx.send("❌ La conexión con Spotify no está configurada.")
            return

        await ctx.send("🎧 Generando radio emocional colectiva...")
        for song in liked_songs[:5]:
            results = core.sp.search(q=song.title, type='track', limit=1)
            if results and results.get('tracks', {}).get('items'):
                seed_id = results['tracks']['items'][0]['id']
                await core.expand_radio_queue(ctx, seed_id, temperatura)
                break
        else:
            await ctx.send("⚠️ No se pudo generar recomendaciones basadas en canciones favoritas.")

    @commands.command(name="historial")
    async def historial(self, ctx):
        """Muestra las últimas canciones reproducidas."""
        if self.last_played:
            description = "\n".join([
                f"{i + 1}. **{title}**" for i, title in enumerate(self.last_played[:10])
            ])
            await ctx.send(embed=self.format_embed("🎧 Últimas Canciones Reproducidas", description))
        else:
            await ctx.send("📭 No hay historial reciente.")

    @commands.command(name="top")
    async def top(self, ctx):
        """Muestra las canciones más reproducidas históricamente."""
        top_songs = get_top_songs(10)
        if top_songs:
            description = "\n".join([
                f"{i + 1}. **{title}** – {count} reproducciones"
                for i, (title, count) in enumerate(top_songs)
            ])
            await ctx.send(embed=self.format_embed("📈 Top Canciones Más Reproducidas", description))
        else:
            await ctx.send("📭 No hay canciones registradas en la base de datos.")

    def format_embed(self, title, content):
        embed = discord.Embed(
            title=title,
            description=content,
            color=discord.Color.purple()
        )
        embed.set_footer(text="Basado en estadísticas de Tooodles")
        return embed

async def setup(bot):
    musicdb = MusicDB(bot)
    await bot.add_cog(musicdb)
    bot.musicdb = musicdb
    preload_top_songs_cache(limit=10)
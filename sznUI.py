# Archivo que maneja las interfaces de usuario dentro de los mensajes del bot, pensado para Toodles v6.
import discord
from discord.ext import commands
from discord.ui import View, Button

class MusicUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def notify_now_playing(self, ctx, song_dict):
        core = self.bot.get_cog("MusicCore")
        if not core:
            return

        song_title = song_dict.get('title', 'Canción Desconocida')
        origin = song_dict.get('origin', '🎵 Solicitada')
        duration = song_dict.get('duration', 0)
        uploader = song_dict.get('uploader', 'Artista Desconocido')
        thumbnail = song_dict.get('thumbnail', '')
        username = song_dict.get('username')

        duration_str = core.format_duration(duration)

        # Crear un Embed premium al estilo Jockey / Rythm
        is_radio = "radio" in origin.lower()
        embed_color = 0x1db954 if "spotify" in origin.lower() else (0x8a2be2 if is_radio else 0x7d5fff)
        
        embed = discord.Embed(
            title="🎶 Ahora Reproduciendo" if not is_radio else "📻 Radio Automática",
            description=f"**[{song_title}]({song_dict.get('url', 'https://www.youtube.com')})**",
            color=embed_color
        )

        # Agregar los campos de metadatos de forma organizada y estética
        embed.add_field(name="👤 Artista", value=f"`{uploader}`", inline=True)
        embed.add_field(name="⏱️ Duración", value=f"`{duration_str}`", inline=True)
        
        # Pedido por / Generado por
        if username:
            embed.add_field(name="📥 Pedido por", value=f"@{username}", inline=True)
        elif is_radio:
            embed.add_field(name="🧬 Origen", value="Recomendaciones ML", inline=True)
        else:
            embed.add_field(name="🧬 Origen", value=f"`{origin}`", inline=True)

        # Establecer la portada si existe
        if thumbnail and thumbnail.startswith("http"):
            embed.set_thumbnail(url=thumbnail)

        # Footer con telemetría de recomendador
        vc_name = ctx.author.voice.channel.name if ctx.author.voice else "Canal de voz"
        vibe_size = len(getattr(core, 'recent_artist_ids', []))
        embed.set_footer(text=f"🔊 VC: {vc_name}  •  🧬 Perfil Radio: {vibe_size}/5 artistas")

        view = self.MusicControls(core, ctx)
        
        # Agregar botón de enlace directo al canal de texto
        if ctx.guild and ctx.channel:
            view.add_item(Button(
                label="📜 Ir al chat", 
                style=discord.ButtonStyle.link, 
                url=f"https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/"
            ))

        try:
            await ctx.send(embed=embed, view=view, delete_after=300)
        except Exception as e:
            print(f"⚠️ Error al enviar notificación de reproducción: {e}", flush=True)

    @commands.command(name="controls", aliases=["ctr", "player"])
    async def controls(self, ctx):
        """Muestra un panel persistente con los controles del reproductor."""
        core = self.bot.get_cog("MusicCore")
        if not core:
            await ctx.send("❌ No se encontró el módulo de música.")
            return

        if not core.current_song:
            await ctx.send("⚠️ No hay ninguna canción reproduciéndose en este momento.")
            return

        await self.notify_now_playing(ctx, core.current_song)

    class MusicControls(View):
        def __init__(self, core, ctx):
            super().__init__(timeout=300)
            self.core = core
            self.ctx = ctx
            self.update_radio_button()

        def update_radio_button(self):
            for child in self.children:
                if getattr(child, 'custom_id', None) == 'toggle_radio':
                    if getattr(self.core, 'radio_mode', False):
                        child.style = discord.ButtonStyle.success
                        child.label = "📻 Radio: On"
                    else:
                        child.style = discord.ButtonStyle.secondary
                        child.label = "📻 Radio: Off"

        @discord.ui.button(label="⏯️ Pausa/Reanuda", style=discord.ButtonStyle.primary, custom_id="pause_resume")
        async def pause_resume(self, interaction: discord.Interaction, button: Button):
            if not self.core or not self.core.voice_client or not self.core.current_song:
                await interaction.response.send_message("⚠️ No hay nada reproduciéndose.", ephemeral=True)
                return
            
            if self.core.voice_client.is_playing():
                self.core.voice_client.pause()
                await interaction.response.send_message("⏸️ Canción pausada por el usuario.", ephemeral=True)
            elif self.core.voice_client.is_paused():
                self.core.voice_client.resume()
                await interaction.response.send_message("▶️ Canción reanudada por el usuario.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ No hay reproducción activa.", ephemeral=True)

        @discord.ui.button(label="⏭️ Saltar", style=discord.ButtonStyle.secondary, custom_id="skip")
        async def skip(self, interaction: discord.Interaction, button: Button):
            if not self.core or not self.core.voice_client:
                await interaction.response.send_message("⚠️ No hay ninguna reproducción activa.", ephemeral=True)
                return
                
            if self.core.voice_client.is_playing() or self.core.voice_client.is_paused():
                self.core.current_song_skipped = True
                self.core.voice_client.stop()
                await interaction.response.send_message("⏭️ Canción saltada vía controles de UI.", ephemeral=True)
            else:
                await interaction.response.send_message("🎵 La cola está vacía.", ephemeral=True)

        @discord.ui.button(label="📻 Radio", style=discord.ButtonStyle.secondary, custom_id="toggle_radio")
        async def toggle_radio(self, interaction: discord.Interaction, button: Button):
            if not self.core:
                await interaction.response.defer()
                return

            self.core.radio_mode = not getattr(self.core, 'radio_mode', False)
            self.update_radio_button()
            status = "activado" if self.core.radio_mode else "desactivado"
            
            # Editar el mensaje del player para cambiar el color del botón
            await interaction.response.edit_message(view=self)
            
            # Si se activa el modo radio y la cola tiene menos de 2 canciones, rellenar de inmediato
            if self.core.radio_mode and len(self.core.song_queue) < 2:
                await self.core.expand_radio_queue(self.ctx)
                
            await interaction.followup.send(f"📻 Modo radio automático **{status}**.", ephemeral=True)

        @discord.ui.button(label="⏹️ Detener", style=discord.ButtonStyle.danger, custom_id="stop")
        async def stop(self, interaction: discord.Interaction, button: Button):
            if self.core and self.core.voice_client:
                self.core.song_queue.clear()
                self.core.radio_mode = False
                cleanup_cache()
                if self.core.voice_client:
                    self.core.voice_client.stop()
                    await self.core.voice_client.disconnect()
                    self.core.voice_client = None
                self.core.current_song = None
                await interaction.response.send_message("⏹️ Reproducción detenida y bot desconectado del canal.", ephemeral=True)
                
                # Publicar mensaje global en el chat
                try:
                    await self.ctx.send("🛑 Reproducción detenida, bot desconectado y cola limpiada vía panel de control.")
                except Exception:
                    pass
            else:
                await interaction.response.send_message("⚠️ El bot no está conectado a ningún canal.", ephemeral=True)

    @commands.command(name="q", aliases=["queue", "cola"])
    async def queueui(self, ctx):
        """Muestra la cola de reproducción interactiva con paginación."""
        core = self.bot.get_cog("MusicCore")
        if not core:
            await ctx.send("❌ No se encontró el módulo de música.")
            return

        if not core.current_song and not core.song_queue:
            await ctx.send("📭 La cola de canciones está vacía.")
            return

        items_per_page = 10
        total_songs = len(core.song_queue)
        total_pages = max(1, (total_songs + items_per_page - 1) // items_per_page)
        current_page = 0

        def get_page_embed(page):
            embed = discord.Embed(
                title="🎵 Cola de Reproducción",
                color=0x7d5fff
            )

            # Mostrar canción sonando actualmente
            if core.current_song:
                current_title = core.current_song.get('title', 'Desconocido')
                current_uploader = core.current_song.get('uploader', 'Artista')
                current_duration = core.format_duration(core.current_song.get('duration', 0))
                embed.add_field(
                    name="▶️ Sonando Ahora",
                    value=f"**{current_title}** - `{current_uploader}` `[{current_duration}]`",
                    inline=False
                )
                if core.current_song.get('thumbnail') and core.current_song['thumbnail'].startswith("http"):
                    embed.set_thumbnail(url=core.current_song['thumbnail'])

            # Construir la lista de la cola de la página
            start = page * items_per_page
            end = start + items_per_page
            songs = core.song_queue[start:end]

            queue_list = ""
            for i, song in enumerate(songs, start=start + 1):
                duration = core.format_duration(song.get('duration', 0))
                origin_icon = "📻" if "radio" in song.get('origin', '').lower() else "🎵"
                requester = f"@{song.get('username')}" if song.get('username') else "Auto"
                queue_list += f"`{i:02d}.` {origin_icon} **{song['title']}** `[{duration}]` (por {requester})\n"

            if not queue_list:
                queue_list = "*No hay canciones en cola. ¡Agrega una o activa la radio!*"

            embed.add_field(
                name="📋 Siguientes Canciones",
                value=queue_list,
                inline=False
            )

            # Duración total de la cola
            total_duration = sum(s.get('duration', 0) for s in core.song_queue)
            total_duration_str = core.format_duration(total_duration)

            embed.set_footer(
                text=f"Página {page + 1}/{total_pages}  •  {total_songs} canciones  •  Tiempo restante: {total_duration_str}"
            )
            return embed

        class QueueControls(View):
            def __init__(self):
                super().__init__(timeout=300)

            @discord.ui.button(label="⏮️ Primera", style=discord.ButtonStyle.secondary)
            async def first(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                current_page = 0
                await interaction.response.edit_message(embed=get_page_embed(current_page), view=self)

            @discord.ui.button(label="⬅️ Anterior", style=discord.ButtonStyle.secondary)
            async def prev(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                if current_page > 0:
                    current_page -= 1
                    await interaction.response.edit_message(embed=get_page_embed(current_page), view=self)
                else:
                    await interaction.response.defer()

            @discord.ui.button(label="➡️ Siguiente", style=discord.ButtonStyle.secondary)
            async def next(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                if current_page < total_pages - 1:
                    current_page += 1
                    await interaction.response.edit_message(embed=get_page_embed(current_page), view=self)
                else:
                    await interaction.response.defer()

            @discord.ui.button(label="⏭️ Última", style=discord.ButtonStyle.secondary)
            async def last(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                current_page = total_pages - 1
                await interaction.response.edit_message(embed=get_page_embed(current_page), view=self)

        await ctx.send(embed=get_page_embed(current_page), view=QueueControls(), delete_after=300)

# Limpieza global de cachés
def cleanup_cache(song_info: dict | None = None):
    import os
    import tempfile
    try:
        if song_info and song_info.get('id'):
            temp_dir = tempfile.gettempdir()
            cache_path = os.path.join(temp_dir, f"cache_{song_info['id']}.webm")
            temp_path = os.path.join(temp_dir, f"cache_{song_info['id']}.tmp")
            if os.path.exists(cache_path):
                os.remove(cache_path)
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

async def setup(bot):
    await bot.add_cog(MusicUI(bot))
# Archivo que maneja las interfaces de usuario dentro de los mensajes del bot, pensado para Toodles v6.
import discord
from discord.ext import commands
from discord.ui import View, Button

class MusicUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.add_check(self.check_command_channel)

    def cog_unload(self):
        self.bot.remove_check(self.check_command_channel)

    async def check_command_channel(self, ctx):
        if not ctx.guild:
            return True

        from sznUtils import load_config
        channel_id_str = load_config(f"cmd_channel_{ctx.guild.id}")
        if not channel_id_str:
            return True

        if str(ctx.channel.id) == channel_id_str:
            return True

        # Los comandos 'channel', 'canal' y 'settings' siempre están permitidos en cualquier canal 
        # para que los administradores puedan configurar o arreglar la vinculación si es necesario.
        if ctx.command.name in ("channel", "canal", "settings"):
            return True

        # Eliminar el comando del usuario e informarle temporalmente
        try:
            await ctx.message.delete(delay=3)
            await ctx.send(
                f"⚠️ Los comandos de música están restringidos al canal exclusivo: <#{channel_id_str}>.", 
                delete_after=5
            )
        except Exception:
            pass

        return False

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

    @commands.command(name="channel", aliases=["canal"])
    @commands.has_permissions(administrator=True)
    async def channel_config(self, ctx, *, arg: str = None):
        """Configura un único canal de texto para recibir comandos de música (Solo Admin)."""
        from sznUtils import save_config, load_config
        
        guild_id = ctx.guild.id
        config_key = f"cmd_channel_{guild_id}"
        
        if arg is None:
            current = load_config(config_key)
            if current:
                embed = discord.Embed(
                    title="⚙️ Configuración de Canal Exclusivo",
                    description=f"Los comandos están bloqueados al canal: <#{current}>.\n\n"
                                f"Para desactivar esta restricción y permitir comandos en cualquier canal, escribe:\n"
                                f"`td?channel reset`",
                    color=0x7d5fff
                )
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="⚙️ Configuración de Canal Exclusivo",
                    description="El bot actualmente responde a comandos en **cualquier canal de texto**.\n\n"
                                f"Para restringir los comandos a un único canal, escribe:\n"
                                f"`td?channel #nombre-del-canal` o `td?channel aqui`",
                    color=0x7d5fff
                )
                await ctx.send(embed=embed)
            return

        arg_lower = arg.lower().strip()
        
        if arg_lower in ("reset", "off", "desactivar", "clear"):
            save_config(config_key, "")
            embed = discord.Embed(
                title="⚙️ Configuración Actualizada",
                description="✅ Se ha desactivado la restricción de canal. El bot ahora responderá en **todos los canales de texto**.",
                color=0x1db954
            )
            await ctx.send(embed=embed)
            return
            
        if arg_lower in ("aqui", "aquí", "here", "this"):
            target_channel = ctx.channel
        else:
            channel_id = None
            if arg.startswith("<#") and arg.endswith(">"):
                try:
                    channel_id = int(arg[2:-1])
                except ValueError:
                    pass
            else:
                try:
                    channel_id = int(arg)
                except ValueError:
                    pass
                    
            if channel_id:
                target_channel = ctx.guild.get_channel(channel_id)
            else:
                target_channel = discord.utils.get(ctx.guild.text_channels, name=arg)

        if not target_channel:
            await ctx.send("❌ No pude encontrar ese canal de texto. Menciónalo como `#nombre-canal` o escribe `td?channel aqui`.")
            return

        save_config(config_key, str(target_channel.id))
        
        embed = discord.Embed(
            title="⚙️ Configuración Actualizada",
            description=f"✅ Los comandos de música ahora están restringidos al canal {target_channel.mention}.\n\n"
                        f"Los comandos enviados en otros canales serán eliminados automáticamente y el bot los ignorará.",
            color=0x1db954
        )
        await ctx.send(embed=embed)

    @channel_config.error
    async def channel_config_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Necesitas permisos de **Administrador** para configurar el canal exclusivo.")

    @commands.command(name="settings", aliases=["config"])
    async def settings_dashboard(self, ctx):
        """Muestra el panel general de configuración del servidor."""
        from sznUtils import load_config
        core = self.bot.get_cog("MusicCore")
        
        guild_id = ctx.guild.id
        cmd_channel = load_config(f"cmd_channel_{guild_id}")
        cmd_channel_val = f"<#{cmd_channel}>" if cmd_channel else "`Cualquier canal`"
        
        # Modo radio por defecto
        # (El bot inicia con radio_mode inactivo por defecto para recolectar datos)
        radio_mode_val = "`Activo`" if core and getattr(core, 'radio_mode', False) else "`Inactivo (Por defecto)`"
        
        # Vibe profile
        vibe_size = len(getattr(core, 'recent_artist_ids', [])) if core else 0
        
        embed = discord.Embed(
            title=f"⚙️ Ajustes de Tooodles - {ctx.guild.name}",
            color=0x7d5fff
        )
        
        embed.add_field(name="⌨️ Prefijo del Bot", value="`td?`", inline=True)
        embed.add_field(name="🔒 Canal de Comandos", value=cmd_channel_val, inline=True)
        embed.add_field(name="📻 Modo Radio (Default)", value=radio_mode_val, inline=True)
        embed.add_field(name="🧬 Historial de Recomendación", value=f"`{vibe_size}/5` artistas registrados", inline=True)
        
        embed.set_footer(text="Para cambiar el canal exclusivo, usa td?channel. Para alternar la radio, haz clic en el botón del reproductor.")
        await ctx.send(embed=embed)

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
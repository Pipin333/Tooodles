# Archivo que maneja las interfaces de usuario dentro de los mensajes del bot, pensado para Toodles v6.
import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput

class PrefixModal(Modal, title="Configurar Prefijo del Bot"):
    prefix_input = TextInput(
        label="Nuevo Prefijo (Máx. 5 caracteres)",
        placeholder="ej: !, $, td?, bot-",
        min_length=1,
        max_length=5,
        required=True
    )

    def __init__(self, settings_view):
        super().__init__()
        self.settings_view = settings_view

    async def on_submit(self, interaction: discord.Interaction):
        new_prefix = self.prefix_input.value.strip()
        guild_id = interaction.guild.id

        from sznUtils import save_config
        save_config(f"prefix_{guild_id}", new_prefix)

        self.settings_view.update_buttons()
        await interaction.response.edit_message(
            embed=self.settings_view.get_embed(interaction.guild),
            view=self.settings_view
        )
        await interaction.followup.send(f"✅ El prefijo del bot ha sido cambiado a `{new_prefix}` para este servidor.", ephemeral=True)


class PersistApprovalView(View):
    def __init__(self, bot, guild, requester_ctx):
        super().__init__(timeout=86400)
        self.bot = bot
        self.guild = guild
        self.requester_ctx = requester_ctx

    @discord.ui.button(label="✅ Autorizar", style=discord.ButtonStyle.success, custom_id="approve_persist")
    async def approve(self, interaction: discord.Interaction, button: Button):
        from sznUtils import set_guild_persist_enabled
        set_guild_persist_enabled(self.guild.id, True)

        for item in self.children:
            item.disabled = True

        embed = interaction.message.embeds[0]
        embed.title = "✅ Solicitud de Persistencia AUTORIZADA"
        embed.color = discord.Color.green()
        embed.set_footer(text=f"Aprobada por @{interaction.user.name}")
        await interaction.response.edit_message(embed=embed, view=self)

        try:
            await self.requester_ctx.send(
                f"🎉 ¡El dueño del bot ha **autorizado** la persistencia de colas para el servidor **{self.guild.name}**!"
            )
        except Exception:
            pass

    @discord.ui.button(label="❌ Rechazar", style=discord.ButtonStyle.danger, custom_id="deny_persist")
    async def deny(self, interaction: discord.Interaction, button: Button):
        from sznUtils import set_guild_persist_enabled
        set_guild_persist_enabled(self.guild.id, False)

        for item in self.children:
            item.disabled = True

        embed = interaction.message.embeds[0]
        embed.title = "❌ Solicitud de Persistencia RECHAZADA"
        embed.color = discord.Color.red()
        embed.set_footer(text=f"Rechazada por @{interaction.user.name}")
        await interaction.response.edit_message(embed=embed, view=self)

        try:
            await self.requester_ctx.send(
                f"❌ La solicitud de persistencia de colas para el servidor **{self.guild.name}** fue rechazada por el dueño del bot."
            )
        except Exception:
            pass


class MusicUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.add_check(self.check_command_channel)

    def cog_unload(self):
        self.bot.remove_check(self.check_command_channel)

    async def check_command_channel(self, ctx):
        if not ctx.guild or not ctx.command:
            return True

        from sznUtils import load_config
        channel_id_str = load_config(f"cmd_channel_{ctx.guild.id}")
        if not channel_id_str or channel_id_str.strip() == "":
            return True

        if str(ctx.channel.id) == channel_id_str:
            return True

        exempt_commands = (
            "channel", "canal", "settings", "persist", "persistencia", 
            "join", "connect", "conectar", "unir", "j", "help", "ayuda",
            "reloadrecsys"
        )
        cmd_name = getattr(ctx.command, 'name', '').lower()
        invoked_with = getattr(ctx, 'invoked_with', '').lower()
        if cmd_name in exempt_commands or invoked_with in exempt_commands:
            return True

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

        player = core.get_player(ctx)
        song_title = song_dict.get('title', 'Canción Desconocida')
        origin = song_dict.get('origin', '🎵 Solicitada')
        duration = song_dict.get('duration', 0)
        uploader = song_dict.get('uploader', 'Artista Desconocido')
        thumbnail = song_dict.get('thumbnail', '')
        username = song_dict.get('username')
        import time
        start_time = getattr(player, 'current_song_start_time', None)
        elapsed_sec = int(time.time() - start_time) if start_time else 0
        duration_sec = duration

        elapsed_str = core.format_duration(elapsed_sec)
        total_str = core.format_duration(duration_sec)

        if duration_sec > 0:
            percent = min(1.0, max(0.0, elapsed_sec / duration_sec))
            bar_len = 10
            filled = int(bar_len * percent)
            progress_bar = f"`{'▬' * filled}🔘{'▬' * (bar_len - filled)}`"
            time_display = f"`{elapsed_str} / {total_str}`\n{progress_bar}"
        else:
            time_display = f"`{elapsed_str}` 🔴 EN VIVO"

        is_radio = "radio" in origin.lower()
        embed_color = 0x1db954 if "spotify" in origin.lower() else (0x8a2be2 if is_radio else 0x7d5fff)
        
        embed = discord.Embed(
            title="🎶 Ahora Reproduciendo" if not is_radio else "📻 Radio Automática",
            description=f"**[{song_title}]({song_dict.get('url', 'https://www.youtube.com')})**",
            color=embed_color
        )

        embed.add_field(name="👤 Artista", value=f"`{uploader}`", inline=True)
        embed.add_field(name="⏱️ Progreso", value=time_display, inline=True)
        
        if username:
            embed.add_field(name="📥 Pedido por", value=f"@{username}", inline=True)
        elif is_radio:
            embed.add_field(name="🧬 Origen", value="Recomendaciones ML", inline=True)
        else:
            embed.add_field(name="🧬 Origen", value=f"`{origin}`", inline=True)

        if thumbnail and thumbnail.startswith("http"):
            embed.set_thumbnail(url=thumbnail)

        vc_name = ctx.author.voice.channel.name if ctx.author.voice else "Canal de voz"
        vibe_size = len(getattr(player, 'recent_artist_ids', []))
        embed.set_footer(text=f"🔊 VC: {vc_name}  •  🧬 Perfil Radio: {vibe_size}/5 artistas")

        view = self.MusicControls(core, ctx)
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

        player = core.get_player(ctx)
        if not player.current_song:
            await ctx.send("⚠️ No hay ninguna canción reproduciéndose en este momento.")
            return

        await self.notify_now_playing(ctx, player.current_song)

    @commands.command(name="persist", aliases=["persistencia"])
    async def toggle_persist_queue(self, ctx, estado: str = None):
        """Habilita o deshabilita la persistencia de colas para este servidor (Solicitud al Dueño)."""
        if not ctx.guild:
            await ctx.send("⚠️ Este comando solo se puede usar en un servidor.")
            return

        is_admin = ctx.author.guild_permissions.administrator
        is_owner = await self.bot.is_owner(ctx.author)

        if not (is_admin or is_owner):
            await ctx.send("❌ Solo los administradores del servidor o el dueño del bot pueden cambiar esta configuración.")
            return

        from sznUtils import is_guild_persist_enabled, set_guild_persist_enabled

        if not estado:
            current = is_guild_persist_enabled(ctx.guild.id)
            status_str = "🟢 Activada" if current else "🔴 Desactivada"
            await ctx.send(f"ℹ️ La persistencia de colas en este servidor está: **{status_str}**.\nUsa `{ctx.prefix}persist on` o `{ctx.prefix}persist off` para cambiarla.")
            return

        mode = estado.lower().strip()
        if mode in ("on", "activar", "activado", "true", "1"):
            if is_owner:
                set_guild_persist_enabled(ctx.guild.id, True)
                await ctx.send("✅ Persistencia de colas **activada** directamente por el dueño del bot.")
            else:
                try:
                    app_info = await self.bot.application_info()
                    owner = app_info.owner

                    embed = discord.Embed(
                        title="📩 Solicitud de Persistencia de Colas",
                        description=f"El administrador **@{ctx.author.name}** solicita activar la persistencia de colas.",
                        color=discord.Color.gold()
                    )
                    embed.add_field(name="🏰 Servidor", value=f"`{ctx.guild.name}` (`{ctx.guild.id}`)", inline=True)
                    embed.add_field(name="👤 Solicitante", value=f"@{ctx.author.name} (`{ctx.author.id}`)", inline=True)
                    embed.set_footer(text="Usa los botones para responder a esta solicitud.")

                    view = PersistApprovalView(self.bot, ctx.guild, ctx)
                    await owner.send(embed=embed, view=view)
                    await ctx.send("📩 **Solicitud enviada**: Se envió un mensaje al dueño del bot para solicitar autorización. Te notificaremos cuando responda.")
                except Exception as e:
                    print(f"⚠️ Error al enviar solicitud por DM al dueño: {e}", flush=True)
                    await ctx.send("⚠️ No se pudo enviar el mensaje privado al dueño del bot. Por favor intenta más tarde.")

        elif mode in ("off", "desactivar", "desactivado", "false", "0"):
            set_guild_persist_enabled(ctx.guild.id, False)
            await ctx.send("❌ Persistencia de colas **desactivada** para este servidor.")
        else:
            await ctx.send("⚠️ Opción no válida. Usa `on` o `off`.")

    class MusicControls(View):
        def __init__(self, core, ctx):
            super().__init__(timeout=300)
            self.core = core
            self.ctx = ctx
            self.update_toggle_buttons()

        def get_player(self):
            return self.core.get_player(self.ctx)

        def update_toggle_buttons(self):
            player = self.get_player()
            for child in self.children:
                if getattr(child, 'custom_id', None) == 'toggle_autoplay':
                    if getattr(player, 'autoplay_mode', False):
                        child.style = discord.ButtonStyle.success
                        child.label = "🤖 Autoplay: On"
                    else:
                        child.style = discord.ButtonStyle.secondary
                        child.label = "🤖 Autoplay: Off"

        def update_radio_button(self):
            self.update_toggle_buttons()

        @discord.ui.button(label="⏯️ Pausa/Reanuda", style=discord.ButtonStyle.primary, custom_id="pause_resume", row=0)
        async def pause_resume(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if not player.voice_client or not player.current_song:
                await interaction.response.send_message("⚠️ No hay nada reproduciéndose.", ephemeral=True)
                return
            
            if player.voice_client.is_playing():
                player.voice_client.pause()
                await interaction.response.send_message("⏸️ Canción pausada por el usuario.", ephemeral=True)
            elif player.voice_client.is_paused():
                player.voice_client.resume()
                await interaction.response.send_message("▶️ Canción reanudada por el usuario.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ No hay reproducción activa.", ephemeral=True)

        @discord.ui.button(label="⏭️ Saltar", style=discord.ButtonStyle.secondary, custom_id="skip", row=0)
        async def skip(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if not player.voice_client:
                await interaction.response.send_message("⚠️ No hay ninguna reproducción activa.", ephemeral=True)
                return
                
            if player.voice_client.is_playing() or player.voice_client.is_paused():
                player.current_song_skipped = True
                player.voice_client.stop()
                await interaction.response.send_message("⏭️ Canción saltada vía controles de UI.", ephemeral=True)
            else:
                await interaction.response.send_message("🎵 La cola está vacía.", ephemeral=True)

        @discord.ui.button(label="❤️ Like", style=discord.ButtonStyle.secondary, custom_id="like_song", row=0)
        async def like_song_button(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if not player.current_song or not player.current_song.get('title'):
                await interaction.response.send_message("⚠️ No hay ninguna canción en reproducción.", ephemeral=True)
                return
            
            song_title = player.current_song['title']
            user_id = str(interaction.user.id)
            
            from database import get_db_session, UserLike, Song
            with get_db_session() as session:
                song = session.query(Song).filter_by(title=song_title).first()
                if song:
                    existing = session.query(UserLike).filter_by(user_id=user_id, song_id=song.id).first()
                    if existing:
                        session.delete(existing)
                        await interaction.response.send_message(f"❌ Eliminaste de tus favoritas: **{song_title}**", ephemeral=True)
                    else:
                        session.add(UserLike(user_id=user_id, song_id=song.id))
                        await interaction.response.send_message(f"❤️ Guardaste en tus favoritas: **{song_title}**", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❤️ Registrado me gusta para: **{song_title}**", ephemeral=True)

        @discord.ui.button(label="👎 Dislike", style=discord.ButtonStyle.secondary, custom_id="dislike_song", row=0)
        async def dislike_song_button(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if not player.current_song or not player.current_song.get('title'):
                await interaction.response.send_message("⚠️ No hay ninguna canción en reproducción.", ephemeral=True)
                return
            
            song_title = player.current_song['title']
            user_id = str(interaction.user.id)
            
            import asyncio
            from database import log_dislike_event, remove_dislike
            added = await asyncio.to_thread(log_dislike_event, user_id, song_title)
            if added:
                await interaction.response.send_message(f"👎 Marcaste como no gustada: **{song_title}**", ephemeral=True)
            else:
                await asyncio.to_thread(remove_dislike, user_id, song_title)
                await interaction.response.send_message(f"✅ Eliminaste la marca de no gustada: **{song_title}**", ephemeral=True)

        @discord.ui.button(label="🤖 Autoplay", style=discord.ButtonStyle.secondary, custom_id="toggle_autoplay", row=1)
        async def toggle_autoplay(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            player.autoplay_mode = not getattr(player, 'autoplay_mode', False)
            self.update_toggle_buttons()
            status = "activado" if player.autoplay_mode else "desactivado"
            
            await interaction.response.edit_message(view=self)
            
            if player.autoplay_mode and not player.song_queue and not (player.voice_client and player.voice_client.is_playing()):
                await self.core._fill_queue_from_recsys(self.ctx, player)
                if player.song_queue and player.voice_client and not player.voice_client.is_playing():
                    await self.core.play_next(self.ctx)
                    
            await interaction.followup.send(f"🤖 Autoplay ML **{status}**.", ephemeral=True)

        @discord.ui.button(label="⏹️ Detener", style=discord.ButtonStyle.danger, custom_id="stop", row=1)
        async def stop(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if player.voice_client:
                player.song_queue.clear()
                player.radio_mode = False
                player.autoplay_mode = False
                from sznMusic import cleanup_cache
                cleanup_cache()
                if player.voice_client:
                    player.voice_client.stop()
                    await player.voice_client.disconnect()
                    player.voice_client = None
                player.current_song = None
                await interaction.response.send_message("⏹️ Reproducción detenida y bot desconectado del canal.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ El bot no está conectado a ningún canal.", ephemeral=True)

    @commands.command(name="q", aliases=["queue", "cola"])
    async def queueui(self, ctx):
        """Muestra la cola de reproducción interactiva con paginación."""
        core = self.bot.get_cog("MusicCore")
        if not core:
            await ctx.send("❌ No se encontró el módulo de música.")
            return

        player = core.get_player(ctx)
        if not player.current_song and not player.song_queue:
            await ctx.send("📭 La cola de canciones está vacía.")
            return

        items_per_page = 10
        total_songs = len(player.song_queue)
        total_pages = max(1, (total_songs + items_per_page - 1) // items_per_page)
        current_page = 0

        def get_page_embed(page):
            embed = discord.Embed(
                title="🎵 Cola de Reproducción",
                color=0x7d5fff
            )

            if player.current_song:
                current_title = player.current_song.get('title', 'Desconocido')
                current_uploader = player.current_song.get('uploader', 'Artista')
                current_duration = core.format_duration(player.current_song.get('duration', 0))
                embed.add_field(
                    name="▶️ Sonando Ahora",
                    value=f"**{current_title}** - `{current_uploader}` `[{current_duration}]`",
                    inline=False
                )
                if player.current_song.get('thumbnail') and player.current_song['thumbnail'].startswith("http"):
                    embed.set_thumbnail(url=player.current_song['thumbnail'])

            start = page * items_per_page
            end = start + items_per_page
            songs = player.song_queue[start:end]

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

            total_duration = sum(s.get('duration', 0) for s in player.song_queue)
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
            target_channel = None
            import re
            match = re.search(r'\d+', arg)
            if match:
                try:
                    channel_id = int(match.group(0))
                    target_channel = ctx.guild.get_channel(channel_id)
                except ValueError:
                    pass
            
            if not target_channel:
                clean_name = arg.strip().lstrip("#").lower()
                target_channel = discord.utils.find(
                    lambda c: isinstance(c, discord.TextChannel) and c.name.lower() == clean_name,
                    ctx.guild.channels
                )

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
        """Muestra el panel interactivo de configuración del servidor."""
        core = self.bot.get_cog("MusicCore")
        if not core:
            await ctx.send("❌ Módulo de música no encontrado.")
            return

        view = self.SettingsControls(core, self.bot)
        await ctx.send(embed=view.get_embed(ctx.guild), view=view)

    class SettingsControls(View):
        def __init__(self, core, bot):
            super().__init__(timeout=300)
            self.core = core
            self.bot = bot
            self.update_buttons()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ Solo los **Administradores** pueden modificar la configuración del servidor.", 
                    ephemeral=True
                )
                return False
            return True

        def get_embed(self, guild):
            from sznUtils import load_config
            guild_id = guild.id
            
            cmd_channel = load_config(f"cmd_channel_{guild_id}")
            cmd_channel_val = f"<#{cmd_channel}>" if cmd_channel else "`Cualquier canal`"
            
            default_radio = load_config(f"default_radio_{guild_id}")
            default_radio_val = "`Activo`" if default_radio == "on" else "`Inactivo (Por defecto)`"
            
            prefix = load_config(f"prefix_{guild_id}") or "td?"
            
            player = self.core.get_player(guild.id)
            vibe_size = len(getattr(player, 'recent_artist_ids', []))
            
            embed = discord.Embed(
                title=f"⚙️ Ajustes del Servidor - {guild.name}",
                description="Haz clic en los botones inferiores para cambiar la configuración de forma interactiva.",
                color=0x7d5fff
            )
            embed.add_field(name="⌨️ Prefijo del Bot", value=f"`{prefix}`", inline=True)
            embed.add_field(name="🔒 Canal de Comandos", value=cmd_channel_val, inline=True)
            embed.add_field(name="📻 Radio Default (Al conectar)", value=default_radio_val, inline=True)
            embed.add_field(name="🧬 Historial de Recomendación", value=f"`{vibe_size}/5` artistas registrados", inline=True)
            return embed

        def update_buttons(self):
            from sznUtils import load_config
            guild_id = self.core.bot.guilds[0].id if self.core.bot.guilds else 0
            
            for child in self.children:
                if getattr(child, 'custom_id', None) == 'toggle_channel':
                    cmd_channel = load_config(f"cmd_channel_{guild_id}")
                    if cmd_channel:
                        child.style = discord.ButtonStyle.danger
                        child.label = "🔓 Liberar Canal"
                    else:
                        child.style = discord.ButtonStyle.primary
                        child.label = "🔒 Fijar Canal Aquí"
                        
                elif getattr(child, 'custom_id', None) == 'toggle_default_radio':
                    default_radio = load_config(f"default_radio_{guild_id}")
                    if default_radio == "on":
                        child.style = discord.ButtonStyle.success
                        child.label = "📻 Radio Default: On"
                    else:
                        child.style = discord.ButtonStyle.secondary
                        child.label = "📻 Radio Default: Off"

                elif getattr(child, 'custom_id', None) == 'change_prefix_btn':
                    prefix = load_config(f"prefix_{guild_id}") or "td?"
                    child.label = f"✏️ Prefijo: {prefix}"

        @discord.ui.button(label="Fijar Canal", style=discord.ButtonStyle.primary, custom_id="toggle_channel")
        async def toggle_channel(self, interaction: discord.Interaction, button: Button):
            from sznUtils import save_config, load_config
            guild_id = interaction.guild.id
            config_key = f"cmd_channel_{guild_id}"
            
            current_channel = load_config(config_key)
            if current_channel:
                save_config(config_key, "")
                confirm_msg = "🔓 Se ha desactivado la restricción de canal. El bot responderá en cualquier lado."
            else:
                save_config(config_key, str(interaction.channel.id))
                confirm_msg = f"🔒 Canal exclusivo fijado a <#{interaction.channel.id}>."
                
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(interaction.guild), view=self)
            await interaction.followup.send(confirm_msg, ephemeral=True)

        @discord.ui.button(label="Radio Default", style=discord.ButtonStyle.secondary, custom_id="toggle_default_radio")
        async def toggle_default_radio(self, interaction: discord.Interaction, button: Button):
            from sznUtils import save_config, load_config
            guild_id = interaction.guild.id
            config_key = f"default_radio_{guild_id}"
            
            current_default = load_config(config_key)
            if current_default == "on":
                save_config(config_key, "off")
                confirm_msg = "📻 Radio automática por defecto desactivada."
            else:
                save_config(config_key, "on")
                confirm_msg = "📻 Radio automática por defecto activada. El bot iniciará la radio al conectar."
                
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(interaction.guild), view=self)
            await interaction.followup.send(confirm_msg, ephemeral=True)

        @discord.ui.button(label="✏️ Prefijo: td?", style=discord.ButtonStyle.secondary, custom_id="change_prefix_btn")
        async def change_prefix_btn(self, interaction: discord.Interaction, button: Button):
            await interaction.response.send_modal(PrefixModal(self))

        @discord.ui.button(label="❌ Cerrar", style=discord.ButtonStyle.danger, custom_id="close_settings")
        async def close_settings(self, interaction: discord.Interaction, button: Button):
            await interaction.message.delete()

async def setup(bot):
    await bot.add_cog(MusicUI(bot))
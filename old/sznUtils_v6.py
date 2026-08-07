import asyncio
import json
import os
import re
import tempfile
import time
import urllib.parse
from database import get_db_session, AppConfig

FERNET_KEY = os.getenv("FERNET_KEY")
fernet = None
if FERNET_KEY:
    try:
        from cryptography.fernet import Fernet
        fernet = Fernet(FERNET_KEY)
    except Exception as e:
        print(f"⚠️ Error al inicializar Fernet: {e}")

_CONFIG_CACHE = {}

def save_config(key: str, value: str):
    _CONFIG_CACHE[key] = value
    encrypted_val = fernet.encrypt(value.encode()).decode() if fernet else value
    try:
        with get_db_session() as session:
            existing = session.query(AppConfig).filter_by(key=key).first()
            if existing:
                existing.value = encrypted_val
            else:
                session.add(AppConfig(key=key, value=encrypted_val))
    except Exception as e:
        print(f"⚠️ Error al guardar configuración '{key}' en BD: {e}", flush=True)

def load_config(key: str) -> str | None:
    if key in _CONFIG_CACHE:
        return _CONFIG_CACHE[key]
    try:
        with get_db_session() as session:
            entry = session.query(AppConfig).filter_by(key=key).first()
            if entry:
                raw_val = entry.value
                if fernet and raw_val:
                    try:
                        raw_val = fernet.decrypt(raw_val.encode()).decode()
                    except Exception as e:
                        print(f"❌ Error al desencriptar valor de {key}: {e}")
                        return None
                _CONFIG_CACHE[key] = raw_val
                return raw_val
    except Exception as e:
        print(f"⚠️ Error al cargar configuración '{key}' de la BD: {e}")
    return None

def save_guild_queue(guild_id: int, song_queue: list):
    """Guarda la cola de canciones de un servidor en la BD para persistencia entre reinicios."""
    try:
        serializable = []
        for song in song_queue:
            if isinstance(song, dict) and song.get('title'):
                serializable.append({
                    'title': song.get('title'),
                    'url': song.get('url'),
                    'duration': song.get('duration', 0),
                    'uploader': song.get('uploader', ''),
                    'origin': song.get('origin', '🎵 Recuperada tras reinicio'),
                    'user_id': song.get('user_id'),
                    'username': song.get('username'),
                    'guild_id': str(guild_id),
                    'id': song.get('id')
                })
        save_config(f"queue_{guild_id}", json.dumps(serializable))
    except Exception as e:
        print(f"⚠️ Error al guardar cola persistente para servidor {guild_id}: {e}", flush=True)

def load_guild_queue(guild_id: int) -> list:
    """Carga y limpia la cola de canciones guardada de un servidor desde la BD."""
    try:
        raw = load_config(f"queue_{guild_id}")
        if raw:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                save_config(f"queue_{guild_id}", json.dumps([]))
                return data
    except Exception as e:
        print(f"⚠️ Error al cargar cola persistente para servidor {guild_id}: {e}", flush=True)
    return []

def is_guild_persist_enabled(guild_id: int) -> bool:
    """Verifica si la persistencia de colas está habilitada para un servidor específico."""
    val = load_config(f"persist_queue_{guild_id}")
    if val is not None:
        return val.lower() in ("on", "true", "1", "yes")
    return os.getenv("PERSIST_QUEUES", "true").lower() in ("true", "1", "yes")

def set_guild_persist_enabled(guild_id: int, enabled: bool):
    """Guarda la preferencia de persistencia de colas por servidor."""
    save_config(f"persist_queue_{guild_id}", "on" if enabled else "off")

def json_to_netscape(cookies_json: list | str) -> str:
    """Convierte una lista o string JSON de cookies a formato Netscape."""
    try:
        parsed = json.loads(cookies_json) if isinstance(cookies_json, str) else cookies_json
        if not isinstance(parsed, list):
            raise ValueError("JSON no válido para cookies.")

        lines = ["# Netscape HTTP Cookie File"]
        default_exp = int(time.time() + 86400 * 365)  # Expiración por defecto: 1 año
        for cookie in parsed:
            if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie:
                continue
            domain = cookie.get("domain", ".youtube.com")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = cookie.get("path", "/")
            secure = "TRUE" if cookie.get("secure", False) else "FALSE"
            
            exp_raw = cookie.get("expirationDate", cookie.get("expires"))
            if exp_raw is None or float(exp_raw) <= 0:
                expires = str(default_exp)
            else:
                expires = str(int(float(exp_raw)))

            name = cookie["name"]
            value = cookie["value"]
            lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        return "\n".join(lines)
    except Exception as e:
        raise ValueError(f"Error al convertir cookies a Netscape: {e}")

async def fetch_stealth_cookies() -> str | None:
    """Utiliza Playwright Stealth para obtener cookies frescas de YouTube en segundo plano."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ℹ️ Playwright no está instalado.")
        return None

    try:
        print("🕵️ Generando cookies de YouTube en segundo plano vía Playwright Stealth...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720}
            )
            page = await context.new_page()

            try:
                import playwright_stealth
                if hasattr(playwright_stealth, "stealth_async"):
                    await playwright_stealth.stealth_async(page)
                elif hasattr(playwright_stealth, "stealth"):
                    await playwright_stealth.stealth(page)
            except Exception as e:
                print(f"ℹ️ Nota sobre stealth: {e}")

            await page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            try:
                btn = page.locator("button[aria-label*='Accept'], button[aria-label*='Aceptar']").first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            try:
                await page.goto("https://www.youtube.com/watch?v=dQw4w9WgWgQ", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
            except Exception:
                pass

            cookies = await context.cookies()
            await browser.close()

            if cookies:
                netscape_content = json_to_netscape(cookies)
                save_config("cookies", netscape_content)
                print("✅ Cookies generadas y guardadas exitosamente vía Playwright Stealth.")
                return netscape_content
    except Exception as e:
        print(f"❌ Error al obtener cookies stealth con Playwright: {e}")
    
    return None

# ==============================================================================
# AUDIO EXTRACTION LAYER (yt-dlp + cookies.txt + Node.js 22 EJS Solver)
# ==============================================================================

def get_cookie_file_path() -> str | None:
    # 1. Prioridad: Archivo cookies.txt directo en la raíz del proyecto
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 50:
        return os.path.abspath("cookies.txt")

    # 2. Fallback: Base de datos SQLite / Variable de entorno
    cookies_content = load_config('cookies') or os.getenv('cookies')
    if cookies_content and len(cookies_content.strip()) >= 50:
        try:
            temp = tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8', suffix='.txt', newline='\n')
            temp.write(cookies_content)
            temp.close()
            return temp.name
        except Exception:
            pass

    return None

def extract_playlist_metadata(url: str) -> list[dict]:
    """Extrae metadatos planos de playlists de YouTube o YouTube Music."""
    try:
        from yt_dlp import YoutubeDL
        cookie_path = get_cookie_file_path()
        ydl_opts = {
            "extract_flat": "in_playlist",
            "skip_download": True,
            "quiet": True,
            "nocheckcertificate": True,
            "cookiefile": cookie_path if cookie_path else None,
            "extractor_args": {
                "youtube": {
                    "player_client": ["mweb", "tv_embedded"]
                }
            }
        }
        clean_url = url.strip()
        if "music.youtube.com" in clean_url:
            clean_url = clean_url.replace("music.youtube.com", "www.youtube.com")

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            if not info:
                return []
            entries = info.get('entries') or []
            result = []
            for entry in entries:
                if not entry or not isinstance(entry, dict):
                    continue
                v_id = entry.get('id')
                v_url = entry.get('url') or (f"https://www.youtube.com/watch?v={v_id}" if v_id else None)
                v_title = entry.get('title')
                v_uploader = entry.get('uploader') or entry.get('artist') or 'YouTube'
                v_duration = entry.get('duration', 0)
                if v_title and v_title not in ('[Private video]', '[Deleted video]'):
                    result.append({
                        'title': v_title,
                        'url': v_url,
                        'duration': v_duration or 0,
                        'uploader': v_uploader,
                        'id': v_id
                    })
            return result
    except Exception as e:
        print(f"⚠️ Error al extraer playlist de YouTube: {e}", flush=True)
    return []

def extract_flat_metadata(query: str) -> dict | None:
    """Utiliza yt-dlp únicamente con extract_flat=True para extraer metadatos sin descargar audio."""
    if "spotify.com" in query:
        return None
    try:
        from yt_dlp import YoutubeDL
        search_target = query if query.startswith("http") else f"ytsearch:{query}"
        cookie_path = get_cookie_file_path()
        ydl_opts = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "noplaylist": True,
            "nocheckcertificate": True,
            "cookiefile": cookie_path if cookie_path else None
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_target, download=False)
            if info:
                entry = info['entries'][0] if 'entries' in info and info['entries'] else info
                return {
                    'title': entry.get('title', query),
                    'uploader': entry.get('uploader') or entry.get('artist') or 'Unknown Artist',
                    'id': entry.get('id'),
                    'duration': entry.get('duration', 0)
                }
    except Exception as e:
        print(f"⚠️ Flat metadata extraction note: {e}", flush=True)
    return None

async def extract_info(query: str) -> dict:
    """
    Extracción directa de audio vía yt-dlp con cookies + Node.js 22 EJS Solver.
    """
    q = query.strip()
    clean_query = q

    if "spotify.com" in q or "spotify:" in q:
        import re
        match = re.search(r'(track|playlist|album|artist)[/:]([a-zA-Z0-9]+)', q)
        resolved_title = None
        if match:
            stype, sid = match.group(1), match.group(2)
            try:
                import aiohttp
                oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/{stype}/{sid}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(oembed_url, timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            title = data.get('title')
                            artist = data.get('author_name', '')
                            if title:
                                resolved_title = f"{title} {artist}".strip()
            except Exception as e:
                print(f"⚠️ Error al resolver oEmbed de Spotify: {e}", flush=True)

        if resolved_title:
            clean_query = resolved_title
        else:
            raise ValueError("No se pudieron extraer metadatos del enlace de Spotify. Por favor busca por nombre del tema.")

    if "soundcloud.com" in q:
        flat_meta = await asyncio.to_thread(extract_flat_metadata, q)
        if flat_meta and flat_meta.get('title'):
            clean_query = f"{flat_meta['title']} {flat_meta.get('uploader', '')}".strip()

    search_target = clean_query if clean_query.startswith("http") else f"ytsearch:{clean_query}"
    cookie_path = get_cookie_file_path()

    try:
        from yt_dlp import YoutubeDL
        import shutil
        node_path = shutil.which("node") or shutil.which("nodejs") or "/usr/bin/node"
        ydl_opts = {
            "format": "bestaudio/best/ba",
            "noplaylist": True,
            "quiet": True,
            "nocheckcertificate": True,
            "cookiefile": cookie_path if cookie_path else None,
            "js_runtimes": {"node": {"path": node_path}},
            "extractor_args": {
                "youtube": {
                    "player_client": ["mweb", "tv_embedded"]
                }
            }
        }
        def _yt_extract():
            with YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(search_target, download=False)

        info = await asyncio.to_thread(_yt_extract)
        if info:
            entry = info['entries'][0] if 'entries' in info and info['entries'] else info
            stream_url = entry.get('url')
            formats = entry.get('formats', [])
            if not stream_url and formats:
                valid_audio = [f for f in formats if f.get('url') and f.get('acodec') != 'none']
                if valid_audio:
                    best_valid = sorted(valid_audio, key=lambda x: int(x.get('tbr') or x.get('bitrate') or 0), reverse=True)[0]
                    stream_url = best_valid['url']

            if stream_url:
                print(f"✅ Stream resuelto vía yt-dlp: {entry.get('title')}", flush=True)
                return {
                    'id': entry.get('id', 'direct'),
                    'title': entry.get('title', clean_query),
                    'url': stream_url,
                    'duration': entry.get('duration', 0),
                    'uploader': entry.get('uploader', 'Artist'),
                    'thumbnail': entry.get('thumbnail', '')
                }
    except Exception as e:
        print(f"⚠️ Extracción yt-dlp falló: {e}", flush=True)

    raise RuntimeError(f"No se pudo resolver el stream de audio para: '{query}'")

def extract_local_audio_features(file_path: str) -> dict:
    """Extrae características acústicas reales (BPM, Energía RMS, Brillo Espectral, Zero Crossing)
    directamente desde el archivo de audio local usando FFmpeg y NumPy.
    Ejecución: ~0.05 segundos por canción.
    """
    import subprocess
    import numpy as np

    if not file_path or not os.path.exists(file_path):
        return {}

    try:
        cmd = [
            "ffmpeg", "-y", "-ss", "10", "-i", file_path, "-t", "30",
            "-f", "s16le", "-ac", "1", "-ar", "22050", "pipe:1"
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=10)
        if not res.stdout or len(res.stdout) < 1000:
            return {}

        samples = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        sr = 22050

        # 1. Energía RMS (Fuerza / Volumen)
        rms = float(np.sqrt(np.mean(samples ** 2)))
        energy = min(max(rms * 4.0, 0.0), 1.0)

        # 2. Zero Crossing Rate (Brillo / Presencia de percusión / agudos)
        zero_crossings = float(np.sum(np.diff(np.signbit(samples))) / len(samples))
        brightness = min(max(zero_crossings * 10.0, 0.0), 1.0)

        # 3. Estimación de Tempo / BPM basada en autocorrelación de envolvente
        frame_size = 1024
        hop_size = 512
        frames = [np.mean(np.abs(samples[i:i+frame_size])) for i in range(0, len(samples)-frame_size, hop_size)]
        frames = np.array(frames) - np.mean(frames)
        
        corr = np.correlate(frames, frames, mode='full')
        corr = corr[len(corr)//2:]
        min_lag = int(sr / hop_size * 60 / 220)  # 220 BPM max
        max_lag = int(sr / hop_size * 60 / 60)   # 60 BPM min
        
        if max_lag < len(corr):
            peak_lag = min_lag + np.argmax(corr[min_lag:max_lag])
            bpm = (sr / hop_size * 60) / (peak_lag or 1)
        else:
            bpm = 120.0
            
        bpm = float(min(max(bpm, 60.0), 200.0))
        danceability = min(max((energy * 0.6) + (brightness * 0.4), 0.0), 1.0)

        return {
            'energy': round(energy, 3),
            'tempo': round(bpm, 1),
            'danceability': round(danceability, 3),
            'valence': round(brightness, 3),
            'acousticness': round(1.0 - energy, 3)
        }
    except Exception as e:
        print(f"⚠️ Error en extracción de audio local: {e}", flush=True)
        return {}
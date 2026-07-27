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

def save_config(key: str, value: str):
    if fernet:
        value = fernet.encrypt(value.encode()).decode()
    with get_db_session() as session:
        existing = session.query(AppConfig).filter_by(key=key).first()
        if existing:
            existing.value = value
        else:
            session.add(AppConfig(key=key, value=value))

def load_config(key: str) -> str | None:
    try:
        with get_db_session() as session:
            entry = session.query(AppConfig).filter_by(key=key).first()
            if entry:
                if fernet:
                    try:
                        return fernet.decrypt(entry.value.encode()).decode()
                    except Exception as e:
                        print(f"❌ Error al desencriptar valor de {key}: {e}")
                        return None
                return entry.value
    except Exception as e:
        print(f"⚠️ Error al cargar configuración '{key}' de la BD: {e}")
    return None

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
# PIPED REST API EXTRACTION LAYER (0% YouTube Bot Blocks, Lightweight & Fast)
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

PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.col237.dev",
    "https://pipedapi.drgns.space"
]

def extract_youtube_id(query: str) -> str | None:
    """Extrae un Video ID de YouTube de 11 caracteres de URLs o texto."""
    if not query:
        return None
    q = query.strip()
    if len(q) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', q):
        return q
    match = re.search(r'(?:v=|\/([0-9A-Za-z_-]{11})(?:[\?&]|$)|youtu\.be\/|shorts\/|embed\/)([a-zA-Z0-9_-]{11})', q)
    if match:
        return match.group(1) or match.group(2)
    return None

async def fetch_piped_stream(video_id: str) -> dict | None:
    """Consulta la API pública de Piped `/streams/{id}` para obtener el stream directo de audio y metadata."""
    import aiohttp
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        for instance in PIPED_INSTANCES:
            try:
                base_url = instance.rstrip("/")
                url = f"{base_url}/streams/{video_id}"
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        audio_streams = data.get("audioStreams", [])
                        if audio_streams:
                            best_audio = sorted(audio_streams, key=lambda x: x.get("bitrate", 0), reverse=True)[0]
                            title = data.get("title", f"Video {video_id}")
                            duration = data.get("duration", 0)
                            uploader = data.get("uploader", "YouTube")
                            thumbnail = data.get("thumbnailUrl") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                            
                            print(f"✅ Stream resuelto vía Piped API ({base_url}): {title}", flush=True)
                            return {
                                'id': video_id,
                                'title': title,
                                'url': best_audio['url'],
                                'duration': duration,
                                'uploader': uploader,
                                'thumbnail': thumbnail
                            }
            except Exception as e:
                print(f"⚠️ Instancia Piped ({instance}) no disponible para stream: {e}", flush=True)
                continue

    return None

async def fetch_invidious_stream(video_id: str, session) -> dict | None:
    invidious_instances = [
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de",
        "https://invidious.flokinet.to"
    ]
    for instance in invidious_instances:
        try:
            base_url = instance.rstrip("/")
            url = f"{base_url}/api/v1/videos/{video_id}"
            async with session.get(url, timeout=4) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    adaptive_formats = data.get("adaptiveFormats", [])
                    audio_streams = [s for s in adaptive_formats if "audio" in s.get("type", "").lower()]
                    if audio_streams:
                        best_audio = sorted(audio_streams, key=lambda x: int(x.get("bitrate", 0) or 0), reverse=True)[0]
                        title = data.get("title", f"Video {video_id}")
                        duration = data.get("lengthSeconds", 0)
                        uploader = data.get("author", "YouTube")
                        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                        print(f"✅ Stream resuelto vía Invidious API ({base_url}): {title}", flush=True)
                        return {
                            'id': video_id,
                            'title': title,
                            'url': best_audio['url'],
                            'duration': duration,
                            'uploader': uploader,
                            'thumbnail': thumbnail
                        }
        except Exception:
            continue
    return None

async def fetch_piped_search(query: str) -> dict | None:
    """Busca en Piped e Invidious API utilizando sus esquemas y endpoints nativos."""
    import aiohttp
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    connector = aiohttp.TCPConnector(ssl=False)
    encoded_query = urllib.parse.quote(query)

    invidious_instances = [
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de",
        "https://invidious.flokinet.to"
    ]

    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        # 1. Búsqueda Piped API (/search?q=...)
        for instance in PIPED_INSTANCES:
            try:
                base_url = instance.rstrip("/")
                url = f"{base_url}/search?q={encoded_query}&filter=all"
                async with session.get(url, timeout=4) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                        for item in items:
                            item_url = item.get("url", "") or item.get("videoId", "")
                            video_id = extract_youtube_id(item_url) or item.get("id") or item.get("videoId")
                            if video_id:
                                stream_info = await fetch_piped_stream(video_id)
                                if stream_info:
                                    return stream_info
            except Exception as e:
                print(f"⚠️ Instancia Piped ({instance}) no disponible para búsqueda: {e}", flush=True)

        # 2. Búsqueda Invidious API (/api/v1/search?q=...)
        for instance in invidious_instances:
            try:
                base_url = instance.rstrip("/")
                url = f"{base_url}/api/v1/search?q={encoded_query}&type=video"
                async with session.get(url, timeout=4) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        if isinstance(data, list) and len(data) > 0:
                            for item in data:
                                video_id = item.get("videoId")
                                if video_id:
                                    stream_info = await fetch_invidious_stream(video_id, session)
                                    if stream_info:
                                        return stream_info
            except Exception as e:
                print(f"⚠️ Instancia Invidious ({instance}) no disponible para búsqueda: {e}", flush=True)

    return None

def extract_flat_metadata(query: str) -> dict | None:
    """Utiliza yt-dlp únicamente con extract_flat=True para extraer metadatos sin descargar audio ni scrapear HTML."""
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
    Función principal unificada de extracción inteligente:
    1. Si hay cookies.txt/cookies disponibles -> Extrae directamente vía yt-dlp con cookies (100% oficial y veloz).
    2. Si no hay cookies o yt-dlp falla -> Consulta espejos Piped & Invidious REST API.
    3. Fallback final -> Stream nativo directo.
    """
    if not query:
        raise ValueError("Consulta vacía.")

    q = query.strip()
    cookie_path = get_cookie_file_path()
    clean_query = q

    # Si es un enlace de Spotify o SoundCloud, extraer título limpio
    if "spotify.com/track" in q or "soundcloud.com" in q:
        flat_meta = extract_flat_metadata(q)
        if flat_meta and flat_meta.get('title'):
            clean_query = f"{flat_meta['title']} {flat_meta.get('uploader', '')}".strip()

    search_target = clean_query if clean_query.startswith("http") else f"ytsearch:{clean_query}"

    # FLUJO 1: Si tenemos cookies válidas (cookies.txt o BD), usar yt-dlp primero
    if cookie_path:
        try:
            from yt_dlp import YoutubeDL
            ydl_opts = {
                "format": "bestaudio/best",
                "noplaylist": True,
                "quiet": True,
                "nocheckcertificate": True,
                "cookiefile": cookie_path,
                "extractor_args": {"youtube": {"player_client": ["android", "ios", "mweb"]}}
            }
            def _yt_with_cookies():
                with YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(search_target, download=False)

            info = await asyncio.to_thread(_yt_with_cookies)
            if info:
                entry = info['entries'][0] if 'entries' in info and info['entries'] else info
                stream_url = entry.get('url')
                formats = entry.get('formats', [])
                if not stream_url and formats:
                    audio_formats = [f for f in formats if f.get('acodec') != 'none']
                    if audio_formats:
                        stream_url = audio_formats[-1]['url']

                if stream_url:
                    print(f"✅ Stream resuelto vía yt-dlp con cookies: {entry.get('title')}", flush=True)
                    return {
                        'id': entry.get('id', 'direct'),
                        'title': entry.get('title', clean_query),
                        'url': stream_url,
                        'duration': entry.get('duration', 0),
                        'uploader': entry.get('uploader', 'Artist'),
                        'thumbnail': entry.get('thumbnail', '')
                    }
        except Exception as e:
            print(f"⚠️ Extracción yt-dlp con cookies falló: {e}. Intentando espejos Piped/Invidious...", flush=True)

    # FLUJO 2: Espejos REST API (Piped & Invidious)
    video_id = extract_youtube_id(clean_query)
    if video_id:
        info = await fetch_piped_stream(video_id)
        if info:
            return info

    search_info = await fetch_piped_search(clean_query)
    if search_info:
        return search_info

    # FLUJO 3: Fallback nativo yt-dlp (sin cookies si todo lo anterior no resolvió)
    try:
        from yt_dlp import YoutubeDL
        ydl_opts = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "nocheckcertificate": True,
            "extractor_args": {"youtube": {"player_client": ["android", "ios", "mweb"]}}
        }
        def _yt_fallback():
            with YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(search_target, download=False)

        info = await asyncio.to_thread(_yt_fallback)
        if info:
            entry = info['entries'][0] if 'entries' in info and info['entries'] else info
            stream_url = entry.get('url')
            formats = entry.get('formats', [])
            if not stream_url and formats:
                audio_formats = [f for f in formats if f.get('acodec') != 'none']
                if audio_formats:
                    stream_url = audio_formats[-1]['url']

            return {
                'id': entry.get('id', 'direct'),
                'title': entry.get('title', clean_query),
                'url': stream_url or clean_query,
                'duration': entry.get('duration', 0),
                'uploader': entry.get('uploader', 'Direct Stream'),
                'thumbnail': entry.get('thumbnail', '')
            }
    except Exception as fallback_err:
        print(f"⚠️ Fallback directo final falló: {fallback_err}", flush=True)

    raise RuntimeError(f"No se pudo resolver el stream de audio para: '{query}'")
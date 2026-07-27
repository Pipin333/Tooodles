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

def extract_flat_metadata(query: str) -> dict | None:
    """Utiliza yt-dlp únicamente con extract_flat=True para extraer metadatos sin descargar audio."""
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
    if not query:
        raise ValueError("Consulta vacía.")

    q = query.strip()
    clean_query = q

    if "spotify.com/track" in q or "soundcloud.com" in q:
        flat_meta = extract_flat_metadata(q)
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
            "remote_components": ["ejs:github"],
            "extractor_args": {"youtube": {"player_client": ["mweb", "web_embedded", "web_creator", "web"]}}
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
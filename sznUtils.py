import asyncio
import json
import os
import tempfile
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
        for cookie in parsed:
            if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie:
                continue
            domain = cookie.get("domain", ".youtube.com")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = cookie.get("path", "/")
            secure = "TRUE" if cookie.get("secure", False) else "FALSE"
            expires = str(int(cookie.get("expirationDate", cookie.get("expires", 2145916800))))
            name = cookie["name"]
            value = cookie["value"]
            lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        return "\n".join(lines)
    except Exception as e:
        raise ValueError(f"Error al convertir cookies a Netscape: {e}")

async def fetch_stealth_cookies() -> str | None:
    """
    Utiliza Playwright + playwright_stealth para obtener cookies frescas de YouTube en segundo plano.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ℹ️ Playwright no está instalado. Instala 'playwright' y 'playwright-stealth'.")
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
                from playwright_stealth import stealth_async
                await stealth_async(page)
            except Exception as e:
                print(f"ℹ️ Nota sobre stealth: {e}")

            # Visitar portada de YouTube
            await page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            # Intentar hacer clic en el botón de aceptar consentimiento si aparece
            try:
                btn = page.locator("button[aria-label*='Accept'], button[aria-label*='Aceptar']").first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            # Visitar una página de video para recibir las cookies de reproducción (PREF / VISITOR_INFO1_LIVE)
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
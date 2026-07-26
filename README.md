# Tooodles 🎵 – Bot de Música de Alta Eficiencia para Discord

**Tooodles** es un bot de música modular, eficiente y resiliente para Discord construido sobre **Python 3.11** y **Discord.py**. 

Está diseñado para funcionar sin interrupciones en servidores de recursos reducidos (como **Oracle Cloud Infrastructure / OCI** con 1 OCPU y 1 GB RAM), utilizando una arquitectura desacoplada con extracción ligera vía la **API de Piped/Invidious**, **precarga de búfer en memoria caché**, **Playwright Stealth** y soporte para **Spotify**.

---

## 🌟 Características Principales

- 🌐 **Extracción Ligera vía Piped & Invidious REST API**: Evita los bloqueos anti-bot de YouTube en servidores de nube sin realizar descargas pesadas ni scraping de HTML.
- ⚡ **Latencia 0ms con Precarga de Chunk (2MB)**: Descarga automáticamente los primeros 2 MB (`Range: bytes=0-2097152`) de la siguiente canción a `/tmp/cache_{id}.webm` en segundo plano para una reproducción instantánea sin buffering.
- 🕵️ **Generación de Cookies con Playwright Stealth**: Captura y renueva cookies de sesión de YouTube automáticamente en segundo plano almacenándolas cifradas en SQLite/PostgreSQL.
- 🎧 **Soporte Completo para Spotify & SoundCloud**: Resuelve canciones y playlists de Spotify a través de su API oficial y hace match con el catálogo de YouTube/Piped.
- 🔍 **Búsqueda Difusa con `rapidfuzz`**: Permite buscar y encontrar temas guardados en la base de datos local usando coincidencia por similitud.
- 📻 **Modo Radio Colectivo / Automático**: Genera listas de recomendación automáticas basadas en la canción actual o los gustos grupales en la llamada de voz.
- 🧹 **Limpieza Automática y Control de Inactividad**: Elimina búferes temporales de `/tmp` y desconecta el bot tras 120 segundos de inactividad para liberar RAM y CPU.
- 🚀 **Despliegue en 1 Clic con `./start.sh`**: Script Bash automatizado para actualizar repositorio, compilar contenedor Docker y mostrar logs en tiempo real.

---

## 🧩 Arquitectura del Proyecto

```
Tooodles/
├── main.py               # Punto de entrada, inicialización de BD y carga secuencial de Cogs
├── sznUtils.py           # Capa de extracción ligera (Piped API, Invidious, Playwright Stealth)
├── sznMusic.py           # Capa del reproductor (MusicCore: Cola, FFmpeg, Chunk Prefetching)
├── sznDB.py              # Capa de base de datos (MusicDB: Favoritos, Historial, Radio)
├── sznUI.py              # Capa de interfaz (MusicUI: Embeds interactivos, Botones y Vistas)
├── database.py           # Modelos SQLAlchemy (Song, UserLike, AppConfig) y gestor de sesiones
├── start.sh              # Script de despliegue automatizado para Docker
└── Dockerfile            # Configuración Docker optimizada con capas de caché y FFmpeg
```

### Orden de Carga de Cogs
1. **`MusicDB` (`sznDB.py`)**: Gestión de estadísticas, favoritos y búsquedas difusas.
2. **`MusicCore` (`sznMusic.py`)**: Gestión de la voz, colas, prebuffering y comandos de reproducción.
3. **`MusicUI` (`sznUI.py`)**: Controles visuales con botones interactivos de Discord.

---

## ⚙️ Comando de Referencia

| Comando | Descripción |
| :--- | :--- |
| `td?p <canción o link>` | Reproduce audio desde YouTube, Spotify o SoundCloud. |
| `td?skip` (`td?s`) | Salta la canción actual. |
| `td?queue` (`td?q`) | Muestra la cola de reproducción actual. |
| `td?nowplaying` (`td?np`) | Muestra información del tema sonando actualmente. |
| `td?pause` / `td?resume` | Pausa o reanuda la reproducción. |
| `td?stop` | Detiene la música, vacía la cola y limpia el caché. |
| `td?shuffle` | Mezcla la cola aleatoriamente. |
| `td?move <origen> <destino>`| Mueve una canción de posición en la cola. |
| `td?remove <índice>` | Elimina una canción específica de la cola. |
| `td?like` / `td?unlike` | Guarda o quita el tema actual de tus favoritas. |
| `td?liked` | Lista tus canciones favoritas guardadas. |
| `td?favradio` | Inicia una radio basada en los gustos colectivos de la llamada. |
| `td?radio <0.0-1.0>` | Inicia la radio automática basada en la canción actual. |
| `td?historial` | Muestra los últimos 20 temas reproducidos. |
| `td?top` | Muestra el top global de canciones más escuchadas. |
| `td?help` | Abre el menú de ayuda interactivo. |

---

## 🛠️ Requisitos e Instalación

### Variables de Entorno (`.env`)

Crea un archivo `.env` en la raíz del proyecto:

```env
token_priv=TU_DISCORD_BOT_TOKEN
client_id=TU_SPOTIFY_CLIENT_ID
client_secret=TU_SPOTIFY_CLIENT_SECRET
DATABASE_URL=sqlite:///tooodles.db
```

### 🚀 Despliegue con Docker (Recomendado)

En tu servidor VPS o Docker:

```bash
chmod +x start.sh
./start.sh
```

El script `./start.sh` realizará automáticamente:
1. `git pull` para obtener el último código.
2. Detención y remoción del contenedor anterior.
3. Compilación de la imagen Docker (`tooodles-bot`).
4. Lanzamiento del contenedor con reinicio automático.
5. Apertura de logs en tiempo real (`docker logs -f tooodles`).

---

## 📜 Licencia & Créditos

Desarrollado para la comunidad. Impulsado por **Discord.py**, **SQLAlchemy**, **Piped API**, **Invidious API**, **Playwright** y **FFmpeg**.

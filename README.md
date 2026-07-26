# Tooodles – Bot de música para Discord

Tooodles es un bot modular de música para Discord construido sobre el framework **Discord.py**. Permite reproducir audio desde YouTube y Spotify, gestiona colas de canciones, almacena preferencias de usuario y ofrece un modo radio inteligente. Su arquitectura se apoya en cogs modulares y una base de datos PostgreSQL para persistir información.

## 🚀 Funcionalidades principales

* **Reproducción de música desde YouTube y Spotify**: utiliza `yt_dlp` y las APIs de Spotify para extraer audio y metadatos.
* **Gestión de colas y favoritos**: organiza las canciones en listas, permite saltar, pausar y reordenar temas.
* **Modo Radio**: sugiere y reproduce canciones similares automáticamente.
* **Persistencia de datos**: guarda las preferencias de usuarios y listas en una base de datos PostgreSQL.
* **Arquitectura modular con cogs**: separa la lógica de base de datos (`MusicDB`), la de reproducción (`MusicCore`) y la interfaz (`MusicUI`), cargándolas en el orden correcto.
* **Integración con servicios externos**: usa Spotify para listas y metadatos, YouTube para extracción de audio y FFmpeg para la transmisión.

## 🛠️ Tecnologías y dependencias

* **Python ≥ 3.11**, Git y FFmpeg.
* **Discord.py** (para la API de Discord) y **SQLAlchemy** (ORM con PostgreSQL).
* **yt_dlp** y **spotipy** para integrar YouTube y Spotify.
* Base de datos **PostgreSQL 13+**.
* **Docker** (opcional) para un despliegue consistente.

## 📦 Instalación

### Requisitos previos

Debes tener instalados Python 3.11+, FFmpeg, PostgreSQL y Git. También necesitarás cuentas de desarrollador para Discord y Spotify.

### 1. Despliegue con Docker (recomendado)

1. Clona el repositorio y accede a la carpeta del proyecto.
2. Construye la imagen y arráncala:

   ```bash
   docker build -t tooodles .
   docker run --env-file .env tooodles
   ```

El `Dockerfile` instala las dependencias, copia el proyecto y ejecuta `main.py`.

### 2. Instalación directa en Python

1. Clona el repositorio:

   ```bash
   git clone https://github.com/Pipin333/Tooodles
   cd Tooodles
   ```

2. Instala las dependencias:

   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Crea un archivo `.env` con estas variables:

   * `DATABASE_URL` – cadena de conexión a PostgreSQL.
   * `SPOTIFY_CLIENT_ID` y `SPOTIFY_CLIENT_SECRET` – credenciales de Spotify.
   * `token_priv` – token de bot de Discord.

4. Inicia el bot:

   ```bash
   python main.py
   ```

## ⚙️ Uso básico

Una vez en línea, prueba estos comandos para verificar que el bot responde:

* `td?help` – Muestra ayuda general.
* `td?ping` – Muestra la latencia actual.
* `td?play [canción o enlace]` – Reproduce música y se une al canal de voz.
* `td?queue` – Muestra la cola de reproducción.

Asegúrate de que la base de datos esté operativa y de que las credenciales de Spotify y Discord sean válidas.

## 🧩 Arquitectura y componentes

Tooodles se organiza en cogs que se cargan en un orden específico para resolver dependencias:

| Orden de carga | Cog         | Responsabilidad principal                                       |
| -------------- | ----------- | --------------------------------------------------------------- |
| **1**          | `MusicDB`   | Opera con la base de datos y rastrea canciones.                 |
| **2**          | `MusicCore` | Maneja la reproducción y la integración con servicios externos. |
| **3**          | `MusicUI`   | Implementa la interfaz y comandos de Discord.                   |

La capa de persistencia utiliza SQLAlchemy y gestiona las transacciones con context managers, manteniendo en memoria algunas canciones en un diccionario de caché. El bot se comunica con YouTube y Spotify mediante wrappers (`yt_dlp` y `spotipy`) y usa FFmpeg para procesar audio.

## 🧑‍💻 Contribución y extensión

Puedes añadir nuevas funciones como listas de reproducción, filtros de audio o integración con otros servicios. Asegúrate de:

1. Crear un nuevo cog para cada funcionalidad.
2. Inyectar dependencias necesarias a través del bot.
3. Documentar cualquier nuevo comando en la ayuda (`td?help`).

Pull requests y reportes de issues son bienvenidos.

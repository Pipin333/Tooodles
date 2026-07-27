# 🎶 Tooodles — Discord Music Bot

> A resilient, self-hosted Discord music bot with multi-source audio extraction, Spotify integration, and a 3-tier fallback system designed to keep playing no matter what YouTube throws at it.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [File Structure](#file-structure)
- [Module Reference](#module-reference)
  - [main.py](#mainpy)
  - [database.py](#databasepy)
  - [sznUtils.py](#sznUtilspy)
  - [sznMusic.py](#sznMusicpy)
  - [sznDB.py](#sznDBpy)
  - [sznUI.py](#sznUIpy)
- [Commands Reference](#commands-reference)
- [Audio Extraction Pipeline](#audio-extraction-pipeline)
- [Environment Variables](#environment-variables)
- [Deployment (Oracle Cloud / Docker)](#deployment-oracle-cloud--docker)
- [Local Development](#local-development)
- [Cookies Setup](#cookies-setup)
- [Security Notes](#security-notes)

---

## Overview

**Tooodles** is a Python-based Discord music bot built on `discord.py`. It is designed for reliability: if one audio source fails, it automatically falls back to the next. It supports direct YouTube URLs, text searches, Spotify tracks and playlists, and has a database-backed favorites and history system.

The bot runs in a Docker container and is optimized for self-hosting on Oracle Cloud Infrastructure (OCI) Free Tier virtual machines.

---

## Features

- 🎵 **Multi-source playback** — YouTube (URL or search), Spotify tracks & playlists, SoundCloud
- 🛡️ **3-tier extraction fallback** — yt-dlp with cookies → Piped/Invidious REST APIs → yt-dlp Android/iOS client
- ⚡ **2 MB chunk pre-buffering** — Caches the first 2 MB of the next song to `/tmp` for near-zero playback latency
- ❤️ **Per-user song favorites** — Like and unlike songs; retrieve your personal favorites list
- 📻 **Auto-radio mode** — Continuously plays songs from the database history when the queue runs out
- 🎧 **Group radio (`favradio`)** — Generates a radio based on the collective favorites of everyone in the voice channel
- 📊 **Play count tracking** — Every song played increments its counter in the database
- 🔍 **Fuzzy search** — Uses `rapidfuzz` for typo-tolerant song matching
- 🎛️ **Interactive UI controls** — Pause, skip, stop, and queue navigation via Discord button components
- 🔐 **Cookie management** — Loads `cookies.txt` from disk, database, or generates them automatically via Playwright Stealth
- 🔒 **Optional encryption** — Stores sensitive config (cookies) encrypted with Fernet symmetric encryption
- 🐳 **Docker-ready** — Fully containerized with Node.js 22.x for yt-dlp EJS JavaScript challenge solving

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Discord Gateway                          │
│                  (discord.py Bot, prefix: td?)                  │
└───────────────────────┬─────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  ┌──────────┐   ┌──────────────┐  ┌─────────┐
  │  sznDB   │   │  sznMusic    │  │  sznUI  │
  │ (MusicDB)│   │ (MusicCore)  │  │(MusicUI)│
  └────┬─────┘   └──────┬───────┘  └────┬────┘
       │                │               │
       ▼                ▼               ▼
  ┌──────────┐   ┌──────────────────────────────┐
  │database.py│   │          sznUtils.py          │
  │(SQLAlchemy│   │    Audio Extraction Engine    │
  │SQLite/PG) │   └──────┬───────────┬───────────┘
  └──────────┘           │           │
                         │           │
          ┌──────────────┘           └───────────────┐
          ▼                                          ▼
  ┌───────────────┐                     ┌─────────────────────┐
  │  yt-dlp       │                     │ Piped / Invidious   │
  │  + cookies.txt│                     │   REST APIs         │
  │  + Node.js 22 │                     │ (3 instances each)  │
  │  + EJS solver │                     └─────────────────────┘
  └───────────────┘
```

### Cog System (discord.py Extensions)

| Cog | Class | Responsibility |
|-----|-------|----------------|
| `sznDB` | `MusicDB` | Database operations: likes, history, fuzzy search, top songs, favradio |
| `sznMusic` | `MusicCore` | Queue management, audio extraction, FFmpeg playback, Spotify integration |
| `sznUI` | `MusicUI` | Discord button UI components, now-playing embeds, paginated queue view |

The cogs communicate via `bot.get_cog()` lookups. `MusicCore` is the central hub — `MusicDB` and `MusicUI` both call into it to check the current song state.

---

## File Structure

```
Tooodles/
├── main.py            # Entry point: bot setup, cookie loading, cog registration
├── database.py        # SQLAlchemy models, session management, helper functions
├── sznUtils.py        # Audio extraction engine: yt-dlp, Piped, Invidious, Playwright
├── sznMusic.py        # MusicCore cog: queue, playback, Spotify, radio, commands
├── sznDB.py           # MusicDB cog: likes, history, top songs, favradio
├── sznUI.py           # MusicUI cog: button controls, paginated queue, embeds
├── requirements.txt   # Python dependencies
├── Dockerfile         # Docker image definition (Python 3.11 + FFmpeg + Node.js 22)
├── start.sh           # Deployment script: pull, build, run container
├── .gitignore         # Excludes cookies.txt, .env, *.db, /tmp cache files
└── cookies.txt        # ⚠️ NOT committed — YouTube auth cookies in Netscape format
```

---

## Module Reference

### `main.py`

The bot's entry point. Executed by `python main.py` or Docker's `CMD`.

**Startup sequence:**

1. Calls `setup_database()` — creates all SQL tables if they don't exist.
2. **Cookie loading priority:**
   - Reads `cookies.txt` from disk if present and non-empty (> 50 bytes).
   - Falls back to `load_config("cookies")` — reads from the `AppConfig` table in the database.
   - Falls back to `fetch_stealth_cookies()` — launches a headless Chromium browser via Playwright Stealth to generate fresh YouTube cookies automatically.
3. Stores loaded cookies in `os.environ["cookies"]` for access by `sznUtils`.
4. Loads the three cogs (`sznDB`, `sznMusic`, `sznUI`).
5. Starts the bot with the `token_priv` environment variable.

**Bot prefix:** `td?`

**Key events:**
- `on_ready` — Logs the bot name and ID on successful connection.
- `on_message` — Ignores messages from other bots; passes all others to the command processor.

---

### `database.py`

Manages the SQLAlchemy ORM layer. Supports both **SQLite** (local fallback) and **PostgreSQL** (production via `DATABASE_URL` env var).

#### Models

| Model | Table | Purpose |
|-------|-------|---------|
| `Song` | `songs` | Tracks title, artist, URL/YouTube ID, duration, and play count |
| `AppConfig` | `config` | Key-value store for bot settings (e.g., encrypted cookies) |
| `UserLike` | `likes` | Maps Discord user IDs to song IDs with timestamps |

#### Key Functions

| Function | Description |
|----------|-------------|
| `setup_database()` | Creates all tables. Called once at startup. |
| `get_db_session()` | Context manager yielding a transactional SQLAlchemy session with auto-commit/rollback. |
| `add_or_update_song(title, url, artist, duration)` | Upserts a song by title+artist. Returns the existing or new `Song` object. |
| `get_top_songs(limit, offset)` | Returns the most-played songs ordered by `played_count` descending. |
| `preload_top_songs_cache(limit)` | Pre-warms the in-memory `cached_songs` dict from the database. Called at startup. |

> **Important:** The `url` field in the `songs` table stores the YouTube **video ID** (not the stream URL), since stream URLs from YouTube expire within hours.

---

### `sznUtils.py`

The audio extraction and utility engine. This is the most complex module.

#### Cookie Utilities

| Function | Description |
|----------|-------------|
| `save_config(key, value)` | Persists a key-value pair to `AppConfig`. Encrypts with Fernet if `FERNET_KEY` is set. |
| `load_config(key)` | Reads and optionally decrypts a value from `AppConfig`. |
| `json_to_netscape(cookies_json)` | Converts a JSON cookie array (e.g., from browser extensions) to Netscape `cookies.txt` format. |
| `get_cookie_file_path()` | Returns the path to a valid `cookies.txt` file. Checks disk first, then writes a temp file from the database/env. |
| `fetch_stealth_cookies()` | Uses Playwright with stealth mode to visit YouTube and export cookies to the database. |

#### Piped / Invidious API Layer

These functions bypass YouTube scraping entirely by using public third-party REST APIs.

| Function | Description |
|----------|-------------|
| `extract_youtube_id(query)` | Extracts an 11-character YouTube video ID from URLs or raw text. |
| `fetch_piped_stream(video_id)` | Queries `/streams/{id}` on 3 Piped instances. Returns the highest-bitrate audio stream URL + metadata. |
| `fetch_invidious_stream(video_id, session)` | Queries `/api/v1/videos/{id}` on 3 Invidious instances. Returns the best audio stream. |
| `fetch_piped_search(query)` | Searches Piped then Invidious for a text query. Returns the first resolvable stream. |

**Piped instances used:**
- `https://pipedapi.kavin.rocks`
- `https://pipedapi.col237.dev`
- `https://pipedapi.drgns.space`

**Invidious instances used:**
- `https://inv.nadeko.net`
- `https://invidious.nerdvpn.de`
- `https://invidious.flokinet.to`

#### Main Extraction Function

```python
async def extract_info(query: str) -> dict
```

The central audio resolver. Returns a dict with keys: `id`, `title`, `url`, `duration`, `uploader`, `thumbnail`.

**3-tier fallback strategy:**

```
Level 1 (Primary)
└── yt-dlp + cookies.txt + Node.js 22 EJS solver
    └── Player clients: mweb → web_embedded → web_creator → web
    └── Resolves YouTube signature challenges (n-token, EJS)

    ▼ on failure

Level 2 (REST API Mirror)
└── Piped API search + stream resolution
└── Invidious API search + stream resolution
    └── Zero scraping, zero bot detection

    ▼ on failure

Level 3 (Native Mobile Client)
└── yt-dlp with player_client: android, ios
    └── No cookies needed, but blocked on datacenter IPs
```

> **Why Node.js 22?** yt-dlp's EJS (External JavaScript) solver requires Node.js ≥ 22.0.0 to decrypt YouTube's signature and `n-token` challenges. The `remote_components: ["ejs:github"]` option tells yt-dlp to download the latest EJS solver scripts from GitHub at runtime.

---

### `sznMusic.py`

The `MusicCore` cog handles everything related to playback.

#### Audio Pre-buffering

```python
async def prefetch_chunk(song_info: dict) -> str | None
```

Downloads the first **2 MB** (HTTP `Range: bytes=0-2097152`) of the audio stream to `/tmp/cache_{id}.webm` before playback begins. FFmpeg reads from this local file first, which eliminates the startup buffer delay that would otherwise occur with a cold remote stream.

The next song in the queue is pre-fetched in the background as soon as the current song starts playing.

#### Queue System

- Songs are stored as plain Python dicts with keys: `id`, `title`, `url`, `duration`, `uploader`, `thumbnail`, `origin`, `cache_path`.
- The `origin` field is a human-readable label shown in embeds (e.g., `"🎵 Pedida por Usuario"`, `"📻 Radio Automática"`).
- Adding a song automatically starts playback if the queue was empty.

#### Spotify Integration

- Requires `client_id` and `client_secret` environment variables.
- Spotify track URLs → resolved to `"{track name} {artist name}"` → passed to `extract_info()` for YouTube lookup.
- Spotify playlist URLs → all tracks resolved sequentially and added to the queue.

#### Inactivity Auto-disconnect

A background `tasks.loop` runs every **120 seconds**. If the bot is connected but not playing and the queue is empty, it automatically disconnects and cleans up cache files.

#### Radio Mode

Activated via `td?radio [temperature]`. When the queue empties, `expand_radio_queue()` picks a random song from the database's play history and adds it to the queue.

---

### `sznDB.py`

The `MusicDB` cog handles all database interactions exposed as Discord commands.

#### Internal Methods

| Method | Description |
|--------|-------------|
| `log_song(title)` | Increments `played_count` in the DB and prepends the title to an in-memory `last_played` list (max 20 entries). |
| `find_similar_song(query, threshold=90)` | Uses `rapidfuzz` to find the closest matching song title in the database. Requires ≥ 90% similarity by default. |
| `like_song(user_id, song_title)` | Creates a `UserLike` record for the given user and song. No-op if already liked. |
| `unlike_song(user_id, song_title)` | Deletes the `UserLike` record. No-op if not liked. |
| `get_liked_songs_by_user(user_id)` | Returns all `Song` objects liked by a specific user. |
| `get_liked_songs_by_users(user_ids)` | Returns all songs liked by any user in the provided list (used by `favradio`). |

#### `favradio` Logic

1. Gets all non-bot members in the current voice channel.
2. Queries `UserLike` for all their user IDs in a single batch query.
3. Searches Spotify for the first matched song to get a seed track ID.
4. If no favorites exist, falls back to the top 5 global most-played songs.

---

### `sznUI.py`

The `MusicUI` cog provides all Discord UI components.

#### `notify_now_playing(ctx, song_title, origin)`

Called by `MusicCore` every time a new song starts. Sends an embed with:
- Title: `🎶 Ahora Reproduciendo` (or `🔁` prefix for radio mode)
- Description: song title in bold
- Footer: the `origin` string (who requested it)
- Buttons: Pause/Resume, Skip, Stop, and a link button to the current channel
- Auto-deletes after **300 seconds**

#### `MusicControls` (View)

A persistent `discord.ui.View` with:

| Button | Action |
|--------|--------|
| ⏯️ Pausa/Reanuda | Toggles pause/resume on the current track |
| ⏭️ Saltar | Stops the current track (triggers `after_playing` → `play_next`) |
| ⏹️ Detener | Disconnects the bot, clears the queue and current song |

All button responses are `ephemeral=True` — only visible to the clicking user.

#### `queueui` — Paginated Queue View

Displays the song queue with 10 items per page. Navigation buttons: ⏮️ First, ⬅️ Previous, ➡️ Next, ⏭️ Last. Auto-deletes after 300 seconds.

---

## Commands Reference

> **Prefix:** `td?`  
> Aliases are shown in parentheses.

### Playback Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?p <query>` | `play` | Play a song. Accepts YouTube URLs, search terms, Spotify track/playlist URLs. |
| `td?s` | `skip` | Skip the current song. |
| `td?pause` | — | Pause the current song. |
| `td?resume` | — | Resume from pause. |
| `td?stop` | — | Stop playback, clear queue, disconnect from voice. |
| `td?np` | `nowplaying` | Show the currently playing song as an embed. |
| `td?q` | `queue` | Show the next 10 songs in the queue as an embed. |
| `td?queueui` | — | Show the queue with paginated button navigation. |
| `td?controls` | — | Resend the interactive playback controls view. |
| `td?shuffle` | — | Shuffle the current queue randomly. |
| `td?remove <index>` | — | Remove a song from the queue by its position number. |
| `td?move <from> <to>` | — | Move a song from one queue position to another. |
| `td?search <query>` | — | Search and directly add the top result to the queue. |

### Radio Commands

| Command | Description |
|---------|-------------|
| `td?radio [0.0–1.0]` | Enable auto-radio mode. Temperature controls randomness (default: 0.75). |
| `td?radio off` | Disable auto-radio mode. |
| `td?favradio [temperature]` | Enable group radio based on liked songs of everyone in the voice channel. |

### Favorites & History Commands

| Command | Description |
|---------|-------------|
| `td?like` | Like the currently playing song. Saves it to your personal favorites. |
| `td?unlike` | Remove the currently playing song from your favorites. |
| `td?liked` | Display your saved favorite songs (up to 10). |
| `td?historial` | Show the last 10 songs played in this session. |
| `td?top` | Show the top 10 most-played songs of all time from the database. |

---

## Audio Extraction Pipeline

The following diagram shows what happens when a user runs `td?p Never Gonna Give You Up`:

```
User: td?p Never Gonna Give You Up
        │
        ▼
MusicCore.play()
  └─ add_from_youtube(ctx, "Never Gonna Give You Up")
        │
        ▼
sznUtils.extract_info("Never Gonna Give You Up")
        │
        ├─── [Level 1] cookies.txt found?
        │       YES → yt-dlp(cookiefile, mweb/web_embedded, node EJS solver)
        │               ├─ SUCCESS → return {title, url, duration, ...}
        │               └─ FAIL    → log warning, try Level 2
        │
        ├─── [Level 2] Piped/Invidious API
        │       ├─ fetch_piped_search("Never Gonna Give You Up")
        │       │     └─ GET pipedapi.kavin.rocks/search?q=...
        │       │           └─ Get video_id → fetch_piped_stream(video_id)
        │       │                 └─ GET /streams/{id} → return audio URL
        │       └─ fetch_invidious_stream(video_id, session)
        │
        └─── [Level 3] yt-dlp native (android/ios client, no cookies)
                └─ return audio URL or raise RuntimeError
        │
        ▼
prefetch_chunk(song_info)
  └─ aiohttp GET stream_url with Range: bytes=0-2097152
  └─ Write to /tmp/cache_{id}.webm
        │
        ▼
MusicCore.play_next()
  └─ FFmpegPCMAudio(target="/tmp/cache_{id}.webm" or stream_url)
  └─ voice_client.play(source, after=after_playing)
        │
        ▼
after_playing()
  └─ cleanup_cache(current_song)   # delete /tmp/cache_*.webm
  └─ play_next(ctx)                # continue queue
```

---

## Environment Variables

Set these in a `.env` file or directly in your deployment environment:

| Variable | Required | Description |
|----------|----------|-------------|
| `token_priv` | ✅ Yes | Discord Bot Token from [discord.com/developers](https://discord.com/developers/applications) |
| `client_id` | Optional | Spotify API Client ID (enables Spotify URL resolution) |
| `client_secret` | Optional | Spotify API Client Secret |
| `DATABASE_URL` | Optional | PostgreSQL connection string. Defaults to `sqlite:///tooodles.db` if not set. |
| `FERNET_KEY` | Optional | 32-byte Fernet encryption key for encrypting cookies in the database. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

> ⚠️ **Never commit these values to Git.** Use a `.env` file locally and inject them via Docker's `-e` flags or your platform's secrets manager.

---

## Deployment (Oracle Cloud / Docker)

### Prerequisites

- Docker installed on your Oracle Cloud Linux VM
- The `cookies.txt` file uploaded to `~/Tooodles/` (see [Cookies Setup](#cookies-setup))
- All environment variables configured in `start.sh`

### One-Command Deploy

```bash
./start.sh
```

**What `start.sh` does:**
1. `git pull` — Fetches the latest code from GitHub
2. `docker stop tooodles` / `docker rm tooodles` — Tears down the old container
3. `docker build -t tooodles .` — Builds the new image (installs Node.js 22.x, Python deps, Playwright, EJS scripts)
4. `docker run -d` — Starts the container with all env vars and the `cookies.txt` volume mount
5. `docker logs -f tooodles` — Streams logs in real time

> ⚠️ Always use `./start.sh` — NOT `docker restart`. `docker restart` reuses the existing image and will NOT pick up code changes or new packages.

### Docker Image Details

Base image: `python:3.11-slim`

**System packages installed:**
- `ffmpeg` — Audio decoding and streaming
- `curl`, `ca-certificates` — Required for NodeSource repository setup
- `nodejs` (v22.x via NodeSource) — Required by yt-dlp EJS solver for YouTube signature decryption

**Python packages (key ones):**
- `discord.py >= 2.3.2` — Discord API client with voice support
- `yt-dlp[default]` — Audio extraction with all optional dependencies
- `yt-dlp-ejs` — EJS challenge solver scripts for yt-dlp (companion package)
- `aiohttp` — Async HTTP client for Piped/Invidious API calls
- `spotipy` — Spotify Web API client
- `sqlalchemy` — ORM for SQLite/PostgreSQL
- `rapidfuzz` — Fast fuzzy string matching
- `playwright` + `playwright-stealth` — Headless browser for cookie generation
- `pynacl` — Required by discord.py for voice encryption
- `cryptography` — Fernet encryption for stored cookies

---

## Local Development

### Requirements

- Python 3.11+
- FFmpeg in PATH
- Node.js 22+ in PATH

### Setup

```bash
# Clone the repository
git clone https://github.com/Pipin333/Tooodles.git
cd Tooodles

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium --with-deps

# Create a .env file (never commit this)
cp .env.example .env   # Edit with your tokens

# Place your cookies.txt in the project root
# See "Cookies Setup" below

# Run the bot
python main.py
```

---

## Cookies Setup

YouTube requires authentication cookies to serve audio streams to server-hosted bots (which are flagged as bots by IP). Without valid cookies, yt-dlp will receive "Sign in to confirm you're not a bot" errors.

### Exporting Cookies

1. Install the **[Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** browser extension.
2. Open an **Incognito/Private** window.
3. Navigate to `https://www.youtube.com/robots.txt` (to generate fresh session cookies without logging in).
4. Click the extension icon and export as `cookies.txt` in **Netscape format**.

### Uploading to Oracle Cloud

```bash
# From your local machine (Windows PowerShell):
scp cookies.txt user@your-oracle-ip:~/Tooodles/cookies.txt
```

Or via SSH:
```bash
nano ~/Tooodles/cookies.txt
# Paste the cookie content, then Ctrl+O → Enter → Ctrl+X
```

> ⚠️ **NEVER push `cookies.txt` to GitHub.** It is already excluded in `.gitignore`. Cookies contain your session authentication and expose your Google account if leaked.

### Cookie Lifetime

YouTube session cookies typically expire in **1–2 weeks** for unauthenticated sessions (incognito export). If you start seeing `Sign in to confirm you're not a bot` errors again, simply re-export and re-upload `cookies.txt` and run `./start.sh`.

### Automatic Cookie Regeneration

If `cookies.txt` is not found on disk and no cookies exist in the database, the bot automatically launches a headless Chromium browser via **Playwright Stealth** to visit YouTube and generate fresh cookies. These are saved to the database encrypted (if `FERNET_KEY` is set) for future restarts.

---

## Security Notes

- **`cookies.txt`** is gitignored. Upload it directly to your server via SCP/SFTP — never via Git.
- **`.env`** is gitignored. Never commit environment variables.
- **`tooodles.db`** (SQLite) is gitignored.
- **`/tmp/cache_*.webm`** files are temporary and cleaned up automatically after each song.
- If you set `FERNET_KEY`, cookies stored in the database are encrypted at rest and cannot be read without the key.
- The Piped and Invidious API requests skip TLS verification (`ssl=False`) due to self-signed certificates on some instances. This is acceptable for read-only metadata fetching.

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

---

*Made with ❤️ and too much coffee. Running on Oracle Cloud Free Tier because the shareholders need their 3 extra cents.*

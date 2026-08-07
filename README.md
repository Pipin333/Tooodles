# 🎶 Tooodles — Discord Music Bot

> A high-performance, self-hosted Discord music bot built with `discord.py`, native `yt-dlp` YouTube extraction, Spotify integration, persistent queues, zero-downtime deployments via GitHub Actions CI/CD, and a hybrid queue buffering engine optimized for low-resource cloud VMs.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
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
- [Queue Persistence System](#queue-persistence-system)
- [Graceful Drain (Zero-Downtime Restarts)](#graceful-drain-zero-downtime-restarts)
- [Performance & Buffering Engine](#performance--buffering-engine)
- [Environment Variables](#environment-variables)
- [Deployment](#deployment)
  - [GitHub Actions CI/CD](#github-actions-cicd)
  - [Manual Deployment](#manual-deployment)
- [Local Development](#local-development)
- [Cookies Setup](#cookies-setup)
- [Security Notes](#security-notes)

---

## Overview

**Tooodles** is an asynchronous Python Discord music bot designed for maximum speed, low CPU consumption, and absolute playback stability. It features:

- Direct native `yt-dlp` extraction with Netscape `cookies.txt` authentication
- Instant Spotify playlist queuing with < 400ms startup
- Low-latency FFmpeg Opus passthrough (< 0.5% CPU on 1 vCPU)
- **Persistent queue recovery** across bot restarts and redeployments
- **Zero-downtime CI/CD** via GitHub Actions — push to `main` and the bot waits for the current song to end before restarting
- Database-backed favorites, history tracking, and per-guild configuration

The bot runs inside a Docker container (Python 3.11) and is fully optimized for Oracle Cloud Free Tier (ARM64) and any Linux VM.

---

## Key Features

- 🎵 **Multi-Source Playback** — YouTube (URLs or search queries), Spotify (tracks, playlists, albums & top artist tracks), SoundCloud.
- ⚡ **Instant Spotify Playlist Loader** — Queues 35+ song Spotify playlists in **< 400ms** by resolving track 1 immediately and loading remaining tracks into queue memory.
- 🚀 **FFmpeg Opus Passthrough** — Streams WebM/Opus audio natively without CPU-heavy re-encoding (**< 0.5% CPU** on 1 vCPU). Includes `user-agent` and `Referer` headers to prevent YouTube CDN throttling.
- 💾 **Queue Persistence** — When any disconnect or restart occurs (deploy, `td?stop`, or external kick), the pending queue + current song are serialized as JSON into SQLite via `save_guild_queue()`. Upon reconnecting with `td?j`, `load_guild_queue()` deserializes and restores the full queue automatically — **zero songs lost across restarts**.
- 🔄 **Zero-Downtime Graceful Drain** — On `SIGTERM`/`SIGINT`, the bot saves the queue, disables radio mode, and waits for the current song to finish before shutting down cleanly (up to 10 min timeout).
- 🤖 **GitHub Actions CI/CD** — Push to `main` and GitHub automatically SSHs into your Oracle Cloud server, pulls the latest code, rebuilds the Docker image, and triggers a graceful restart — no manual SSH required.
- 🧠 **Hybrid Queue Optimization Engine**:
  - **Track 1 (Playing)**: Plays smoothly with zero network interference.
  - **Track 2 (Next in Queue)**: Throttled 64 KB chunk pre-buffering to `/tmp/cache_{id}.webm` for 0ms track transition delay.
  - **Track 3+ (Queued)**: Background URL pre-resolution so streams are pre-fetched before reaching the front of the queue.
- 📊 **Now Playing with Progress Bar** — `td?np` shows elapsed time, total duration, and a visual scrubber (`▬▬▬▬🔘▬▬▬▬▬▬`).
- ❤️ **User Favorites & History** — Save liked songs (`td?like`), view personal top tracks (`td?liked`), and check global playback stats (`td?top`).
- 📻 **Auto-Radio & Group Radio** — `td?radio` auto-plays recommendations when the queue ends; `td?favradio` generates a collective playlist based on channel members' favorites.
- 🎛️ **Interactive Discord UI** — Buttons for Pause/Resume, Skip, Stop, and paginated queue navigation (`td?q`).
- 🔐 **Cookie Management** — Reads `cookies.txt` from disk or database, with Playwright Stealth fallback for automatic cookie generation.
- 🏠 **Per-Guild Queue Persistence Toggle** — Owners can enable/disable queue persistence per-server via `td?persist` with owner DM approval flow.
- ⚙️ **Custom Prefix per Guild** — Configurable prefix per server stored in the database. Default: `td?`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Discord Gateway                          │
│               (discord.py Bot, default prefix: td?)             │
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
  │database.py│  │          sznUtils.py          │
  │(SQLAlchemy│  │  yt-dlp + Queue Persistence   │
  │SQLite/PG) │  └──────────────┬───────────────┘
  └──────────┘                 │
                               ▼
                    ┌─────────────────────┐
                    │   YouTube Audio     │
                    │  (Direct Opus or    │
                    │   Local Cache)      │
                    └─────────────────────┘
```

### Cog System (discord.py Extensions)

| Cog | Class | Responsibility |
|-----|-------|----------------|
| `sznDB` | `MusicDB` | Database operations: user likes, playback history, fuzzy title matching, `favradio` |
| `sznMusic` | `MusicCore` | Queue state, `yt-dlp` extraction, FFmpeg playback, Spotify integration, hybrid buffering, queue persistence |
| `sznUI` | `MusicUI` | Discord button components, now-playing embeds, progress bar, paginated queue view, settings commands |

---

## File Structure

```
Tooodles/
├── main.py                        # Bot startup, graceful drain, signal handling, cog loading
├── database.py                    # SQLAlchemy models (Song, AppConfig, UserLike) & session handlers
├── sznUtils.py                    # Audio extraction engine, queue persistence functions
├── sznMusic/                      # Paquete modular MusicCore: cola, FFmpeg, Spotify, buffering, radio y comandos
│   ├── __init__.py                # Cog MusicCore, setup(bot) y comandos de Discord
│   ├── player.py                  # GuildPlayer, precarga/caché de audio y extractor Spotify/YouTube
│   └── radio.py                   # Algoritmo de radio contextual por géneros y recomendaciones RecSys ML
├── sznDB.py                       # MusicDB cog: likes, top songs, history, group radio
├── sznUI.py                       # MusicUI cog: interactive buttons, paginated queue UI, embeds, persist toggle
├── requirements.txt               # Python dependencies
├── Dockerfile                     # Container definition (Python 3.11 + FFmpeg + Node.js 22.x)
├── start.sh                       # One-click deployment script (git pull, docker build, docker run)
├── .github/
│   └── workflows/
│       └── deploy.yml             # GitHub Actions CI/CD — auto-deploy on push to main
└── .gitignore                     # Excludes cookies.txt, .env, *.db, /tmp cache files
```

---

## Module Reference

### `main.py`

Bot entry point executed by Docker's `CMD`.

**Startup Workflow:**
1. Executes `setup_database()` to create database tables.
2. Checks for `cookies.txt` locally; falls back to database config, then Playwright Stealth auto-generation.
3. Sets `os.environ["cookies"]` for global availability.
4. Loads extensions: `sznDB`, `sznMusic`, `sznUI`.
5. Registers `SIGTERM`/`SIGINT` signal handlers pointing to `graceful_shutdown()`.
6. Logs into Discord using `token_priv`.

**`graceful_shutdown(bot)` Flow:**
1. Sets `bot.is_draining = True` (prevents double-execution and blocks queue overwrite events).
2. Saves pending queues to database for all guilds with persistence enabled.
3. Clears in-memory queues and disables radio mode.
4. Polls every 2 seconds until all voice clients finish playing (max 10 minutes).
5. Force-disconnects any remaining voice clients.
6. Closes the Discord connection cleanly.

---

### `database.py`

Handles database persistence via SQLAlchemy. Supports SQLite (local default) and PostgreSQL (`DATABASE_URL`).

#### Models

| Model | Table | Fields |
|-------|-------|--------|
| `Song` | `songs` | `id`, `title`, `url`, `artist`, `duration`, `played_count`, `spotify_id`, `genres`, `popularity` |
| `AppConfig` | `config` | `key`, `value` (stores queue JSON, cookies, prefix, persist flags) |
| `UserLike` | `likes` | `id`, `user_id`, `song_id`, `timestamp` |
| `PlayLog` | `play_logs` | `id`, `user_id`, `username`, `song_id`, `guild_id`, `played_at`, `listened_duration` (s), `completed` (0/1), `skipped_at` (s) |

---

### `sznUtils.py`

Audio extraction module and persistence utilities.

#### Audio Functions

| Function | Description |
|----------|-------------|
| `extract_info(query)` | Primary audio stream extractor. Uses `yt-dlp` with `player_client: ["mweb", "web"]` and cookies. Returns stream metadata dict. |
| `extract_flat_metadata(query)` | Rapid metadata-only lookup using `extract_flat=True` without downloading audio streams. |
| `get_cookie_file_path()` | Returns the absolute path to a valid `cookies.txt` file (checks disk root, then database/environment). |
| `fetch_stealth_cookies()` | Launches headless Chromium via Playwright Stealth to export fresh YouTube session cookies if missing. |

#### Queue Persistence Functions

| Function | Description |
|----------|-------------|
| `save_guild_queue(guild_id, queue)` | Serializes and saves the queue (title, url, duration, uploader, origin, user) to `AppConfig` as JSON. |
| `load_guild_queue(guild_id)` | Loads and clears the saved queue from the database, returning the deserialized list. |
| `is_guild_persist_enabled(guild_id)` | Returns `True` if queue persistence is toggled on for the given guild. |
| `set_guild_persist_enabled(guild_id, enabled)` | Writes the `persist_queue_{guild_id}` flag to the database. |

---

### `sznMusic/`

Core music player handling queue logic, playback, Spotify resolution, and persistence.

#### Key Systems

- **Full Spotify Source Support**: Resolves Spotify **tracks**, **playlists**, **albums** (`add_album_from_spotify`), and **artist top tracks** (`add_artist_from_spotify`). Track 1 always resolves in < 400ms to start playback immediately.
- **Throttled Chunk Pre-buffering (`prefetch_chunk_throttled`)**: Downloads 2 MB of the next song in 64 KB chunks with 20ms micro-pauses (`asyncio.sleep(0.02)`), avoiding CPU/network spikes while the active song plays.
- **Queue Protection Guard**: `play_next()` checks `if self.voice_client.is_playing(): return`, ensuring adding new songs with `td?p` never interrupts active playback.
- **FFmpeg Opus Passthrough**: Streams native WebM/Opus directly to Discord with `-user_agent` and `-headers "Referer: https://www.youtube.com/"` `before_options` to prevent YouTube CDN throttling.
- **Inactivity Auto-Disconnect**: A background task checks every 2 minutes; if no music is playing and the queue is empty, the bot auto-disconnects from the voice channel.
- **Queue Save on Disconnect**: When the bot disconnects (via `td?stop`, `td?dc`, or externally), it saves the full queue to the database if persistence is enabled — unless `is_draining` is set (to prevent race conditions with `graceful_shutdown`).

#### `GuildPlayer` State Object

Each connected guild has an isolated `GuildPlayer` instance containing:

| Field | Description |
|-------|-------------|
| `song_queue` | Ordered list of song metadata dicts |
| `current_song` | Currently playing song dict |
| `voice_client` | `discord.VoiceClient` instance |
| `radio_mode` | Boolean: auto-radio mode active |
| `radio_seed_id` | YouTube video ID used as radio seed |
| `radio_temperature` | Diversity factor for radio recommendations (0.0–1.0) |
| `is_loading_song` | Boolean: prevents inactivity disconnect during track load |
| `current_song_start_time` | Unix timestamp of when current song started (for progress bar) |
| `last_ctx` | Last command context for notifications |

---

### `sznDB.py`

Manages song statistics, favorites, and fuzzy matching.

#### Features

- **`td?like` / `td?unlike`**: Toggle favorites for the currently playing song.
- **`td?liked`**: Display the user's saved favorite tracks.
- **`td?favradio`**: Queries favorites of all members in the current voice channel to build a collective personalized radio.
- **`td?top`**: Shows the 10 most-played tracks globally.
- **`td?historial`**: Shows the 10 most recently played tracks.

---

### `sznUI.py`

Provides interactive UI controls via Discord Buttons and Embeds.

- **`notify_now_playing()`**: Sends now-playing embeds with interactive buttons (Pause/Resume, Skip, Radio Toggle, Stop, Go to Channel) and auto-deletes after 5 minutes.
- **Progress Bar in `td?np`**: Calculates elapsed time from `current_song_start_time` and renders a visual scrubber bar.
- **`td?q`**: Paginated view of the queue with 10 songs per page and navigation buttons (Previous, Next).
- **`td?persist`**: Toggles queue persistence for the current guild. If requested by a non-owner admin, sends a DM to the bot owner with `[✅ Authorize]` / `[❌ Reject]` approval buttons.
- **`td?channel`**: Locks bot commands to a specific text channel.
- **`td?settings`**: Displays current per-guild bot configuration.
- **`td?controls`**: Resends the interactive playback control buttons.

---

## Commands Reference

> **Default prefix:** `td?` (configurable per guild)

### Connection & Playback

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?j` | `join`, `connect`, `conectar`, `unir` | Connect to your voice channel and restore any saved queue. |
| `td?p <query>` | `play` | Play a song (YouTube search, YouTube URL, Spotify track or playlist). |
| `td?s` | `skip` | Skip the currently playing song. |
| `td?pause` | — | Pause audio playback. |
| `td?resume` | `r`, `reanudar` | Resume paused audio or start playing a restored queue. |
| `td?stop` | `disconnect`, `leave`, `exit`, `dc` | Stop playback, save queue to DB (if persist enabled), and disconnect. |
| `td?np` | `nowplaying` | Show now-playing embed with progress bar and elapsed time. |
| `td?controls` | `ctr`, `player` | Resend the interactive playback buttons. |

### Queue Management

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?q` | `queue`, `cola` | Paginated queue view (10 songs per page). |
| `td?shuffle` | — | Randomly shuffle all queued tracks. |
| `td?remove <index>` | — | Remove a track from the queue by position number. |
| `td?move <from> <to>` | — | Move a queued track from one position to another. |
| `td?clear` | `clean`, `cq` | Clear the entire queue (in-memory + database) without stopping current song. |
| `td?search <query>` | — | Search YouTube and queue the best result. |

### Radio & Favorites

| Command | Description |
|---------|-------------|
| `td?radio [off]` | Toggle auto-radio mode. Generates 5 recommendations when queue ends. |
| `td?favradio [temp]` | Build a group radio from all channel members' liked songs (temp: 0.0–1.0). |
| `td?like` | Save the current song to your personal favorites. |
| `td?unlike` | Remove the current song from your favorites. |
| `td?liked` | View your personal favorite songs list. |
| `td?historial` | View the 10 most recently played tracks. |
| `td?top` | View the 10 most played songs of all time. |

### Server Configuration

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?persist` | `persistencia` | Toggle queue persistence. Sends DM approval request to bot owner if requested by admin. |
| `td?channel` | `canal` | Lock bot commands to a specific text channel. |
| `td?settings` | `config` | Display current per-guild configuration. |

---

## Queue Persistence System

Queue persistence allows the bot to recover pending songs after any type of disconnection or restart — no songs lost.

### How it Works

```
[Bot receives SIGTERM / td?stop / disconnects from voice]
         │
         ▼
  is_guild_persist_enabled(guild_id)?
         │ YES
         ▼
  save_guild_queue(guild_id, current_song + song_queue)
  → Serialized as JSON in AppConfig: "queue_{guild_id}"
         │
         ▼
  [Bot restarts / Graceful Drain completes]
         │
         ▼
  User types: td?j (join)
         │
         ▼
  connect_to_voice() → load_guild_queue(guild_id)
  → Deserializes JSON → pushes songs back into player.song_queue
  → Calls play_next() automatically
         │
         ▼
  📥 "Cola recuperada: Se restauraron N canciones" ✅
```

### When the Queue is Saved

| Trigger | Behavior |
|---------|----------|
| `SIGTERM` / `SIGINT` (restart/deploy) | `graceful_shutdown` saves queue, waits for current song to end |
| `td?stop` / `td?dc` | Saves queue before disconnecting if persist is enabled |
| External disconnect (voice state event) | Saves queue unless `bot.is_draining` is set (prevents race condition) |
| `td?clear` | Clears both in-memory queue **and** the database record |

### Enabling Persistence

Enable persistence for a guild via DM to the bot owner, or directly in the database:

```bash
docker exec tooodles python3 -c "
from database import setup_database
from sznUtils import set_guild_persist_enabled
setup_database()
set_guild_persist_enabled(YOUR_GUILD_ID, True)
print('Persistence enabled.')
"
```

---

## Graceful Drain (Zero-Downtime Restarts)

When the server receives a deploy signal (e.g., from GitHub Actions), the bot does **not** cut the music mid-song. Instead:

1. `SIGTERM` is caught → `graceful_shutdown()` is invoked.
2. `bot.is_draining = True` is set.
3. Pending queues are saved to the database per guild.
4. Radio mode is disabled.
5. In-memory queues are cleared.
6. The bot polls every 2 seconds until all active voice clients stop playing.
7. The bot disconnects cleanly and closes the Discord connection.
8. Docker starts the new container with the latest code.
9. Users rejoin music with `td?j` — queue is restored automatically.

**Maximum wait time before forced shutdown:** 10 minutes.

---

## Performance & Buffering Engine

Tooodles uses a 3-tier hybrid buffering pipeline designed for instant startup and zero playback stuttering:

```
[User adds Spotify Playlist (35 tracks)]
        │
        ├── Track 1: Resolved immediately (<400ms) ➔ FFmpegOpusAudio
        │            + user-agent & Referer headers (prevents CDN throttling)
        │
        ├── Track 2: Pushed to Queue ➔ prefetch_chunk_throttled()
        │            (Downloads 2 MB in 64 KB chunks + 20ms sleep = 0% CPU impact)
        │
        └── Tracks 3..35: Pushed to Queue memory instantly ➔ URL pre-resolution
                         (Pre-fetches stream URLs in background as queue advances)
```

**CPU Profile on Oracle Cloud Ampere (ARM64, 1 vCPU):**

| Operation | CPU Usage |
|-----------|-----------|
| FFmpeg Opus passthrough (streaming) | < 0.5% |
| `yt-dlp` stream extraction | ~2–4% (< 1 second) |
| Pre-buffer (64 KB throttled chunks) | < 1% |
| Spotify playlist load (35 tracks) | ~1–2% for < 400ms |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `token_priv` | ✅ Yes | Discord Bot Token |
| `client_id` | Optional | Spotify API Client ID |
| `client_secret` | Optional | Spotify API Client Secret |
| `DATABASE_URL` | Optional | PostgreSQL URI (defaults to `sqlite:///tooodles.db`) |
| `FERNET_KEY` | Optional | 32-byte Fernet key for symmetric encryption (`cryptography.fernet`) of session cookies stored in `AppConfig`. If absent, cookies are stored as plaintext. |

---

## Deployment

### GitHub Actions CI/CD

Tooodles ships with a GitHub Actions workflow at `.github/workflows/deploy.yml` that automatically deploys on every push to `main`.

**How it works:**
1. You push code to `main` from your local machine.
2. GitHub Actions triggers, SSHs into your Oracle Cloud server using stored secrets.
3. Runs `./start.sh` on the server: `git pull` → `docker build` → `docker run`.
4. The running bot receives `SIGTERM` from Docker and enters Graceful Drain.
5. The current song finishes. The new container starts. Music resumes via `td?j`.

**Required GitHub Secrets:**

| Secret | Description |
|--------|-------------|
| `VPS_HOST` | Your Oracle Cloud server IP address |
| `VPS_USERNAME` | SSH login username (e.g., `ubuntu`) |
| `VPS_SSH_KEY` | Private SSH key (PEM format, content of `~/.ssh/id_rsa`) |

**Setting up secrets:**  
Go to your GitHub repo → Settings → Secrets and variables → Actions → New repository secret.

### Manual Deployment

SSH into your server and run:

```bash
chmod +x start.sh && ./start.sh
```

**What `start.sh` executes:**
1. `git pull` — Pulls latest updates from `origin/main`.
2. `docker stop -t 600 tooodles` — Sends `SIGTERM` and waits up to 10 min for graceful drain.
3. `docker rm tooodles` — Removes the old container.
4. `docker build -t tooodles .` — Rebuilds the image with latest code.
5. `docker run -d` — Launches the new container with env variables and volume mounts.

---

## Local Development

```bash
# Install Python dependencies
pip install -r requirements.txt

# Set required environment variables
export token_priv="YOUR_DISCORD_BOT_TOKEN"
export client_id="YOUR_SPOTIFY_CLIENT_ID"       # Optional
export client_secret="YOUR_SPOTIFY_CLIENT_SECRET" # Optional

# Run the bot
python main.py
```

> **Tip:** Place `cookies.txt` in the project root for YouTube authentication.

---

## Cookies Setup

YouTube requires session cookies to serve audio streams from cloud server IPs.

1. Open an **Incognito Browser Window** and navigate to `https://www.youtube.com/robots.txt`.
2. Export cookies using the browser extension **"Get cookies.txt LOCALLY"** (Chrome/Firefox).
3. Save the output file as `cookies.txt` in the `~/Tooodles/` directory on your server.
4. Run `./start.sh` — cookies are loaded automatically on startup.

**Alternative (Automatic):** If no `cookies.txt` is found, the bot will attempt to generate fresh cookies via Playwright Stealth (headless Chromium). This requires Playwright to be installed in the container.

---

## Security Notes

- `cookies.txt` is in `.gitignore` and must **never** be committed to Git.
- The `token_priv` Discord token is passed via Docker environment variables, never hardcoded.
- The `FERNET_KEY` symmetrically encrypts session cookies via `cryptography.fernet` before storing them in `AppConfig`. This means even if the database file is exposed, raw cookie strings are not readable without the key.
- SSH keys used by GitHub Actions should be dedicated deploy keys with minimal permissions.

---

## License & Credits

Built with ❤️ for music lovers.  
Powered by [`discord.py`](https://github.com/Rapptz/discord.py), [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), [`spotipy`](https://github.com/spotipy-dev/spotipy), and [`FFmpeg`](https://ffmpeg.org/).

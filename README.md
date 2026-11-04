# Spotify Playlist Tools

Personal Spotify automation scripts and an optional web dashboard. The repository contains standalone Python scripts, a FastAPI API, and a React/Vite frontend. You can use the scripts without running the web application or configuring a database.

## Components

- `scripts/liked_add/` mirrors saved tracks into a playlist.
- `scripts/vaulted_add/` synchronizes the `_vaulted` playlist with tracks collected from owned playlists and saved tracks. Review the script before first use: it adds and removes playlist items.
- `scripts/monthly_recommend/` previews monthly recommendations from listening and playlist data. It is read-only by default; `--write` explicitly creates or replaces its private recommendation playlist. See [Monthly recommendations](#monthly-recommendations).
- `backend/` provides Spotify OAuth, account-backed dashboard data, and API-triggered playlist actions.
- `website/spotify-script-hub-main/` contains the React dashboard for account connection, library and listening views, script details, and run history.
- `music-visualizer/` is a separate Windows-only desktop visualizer. It is not part of the web application.

The backend supports SQLite by default and PostgreSQL through `DATABASE_URL` (including Supabase Postgres). A database is needed only when running the backend; it is not needed for standalone scripts.

## Requirements

- Python 3.12 or newer
- Node.js 22 and npm to run or build the website
- A Spotify Developer application for OAuth-based scripts and the backend
- Optional: PostgreSQL for a persistent hosted backend

## Configure Spotify access

Create a Spotify app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard). Add the exact redirect URI you configure below to the app's allowed redirect URIs. For the local backend, use `http://127.0.0.1:8000/auth/callback`; standalone scripts use Spotipy's local callback, normally `http://127.0.0.1:8080/callback`.

For standalone scripts, create a root `.env` file (never commit it):

```dotenv
SPOTIPY_CLIENT_ID=your-client-id
SPOTIPY_CLIENT_SECRET=your-client-secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8080/callback
# Required before monthly recommendation writes; use the intended Spotify account ID.
SPOTIFY_EXPECTED_USER_ID=
# Optional for Last.fm recommendation fallbacks
LASTFM_API_KEY=
```

The scripts cache OAuth tokens under `.cache/`; keep that directory private and out of version control. OAuth scopes vary by script. Approve only the scopes requested for the workflow you intend to use.

## Run standalone scripts

Install the Python dependencies from the repository root:

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the existing playlist helpers from the repository root:

```powershell
./liked.ps1
./vaulted.ps1
```

Windows batch launchers are also included (`liked.bat` and `vaulted.bat`). These helpers can modify Spotify playlists; read the script's target and behavior before authorizing Spotify access.

### Monthly recommendations

Preview recommendations without changing Spotify:

```bash
python scripts/monthly_recommend/monthly_recommend.py
```

Preview mode is the default. To explicitly create or replace the private monthly playlist, pass `--write`; writes require `SPOTIFY_EXPECTED_USER_ID` to match the authenticated account. An optional `--month YYYY-MM` selects the month label; `--run-label TEXT` labels the local log output only. Last.fm is optional. The script uses Spotify listening and playlist data to build recommendations, so its requested read scopes should be reviewed before authorization.

## Run the API and web dashboard locally

### Backend

Copy `backend/.env.example` to `backend/.env` and fill in the Spotify app values. Set `APP_SECRET_KEY` to a long random value and choose `FRONTEND_URL`, for example `http://localhost:5173`. For local development, set `DATABASE_URL=sqlite:///local_dev.db`; the example's `/var/lib/...` SQLite path is intended for a container with a mounted persistent directory. For PostgreSQL, set `DATABASE_URL` to a PostgreSQL connection URL. Keep the secret key stable: changing it prevents the backend from decrypting previously stored OAuth tokens.

```bash
python -m venv .venv
# Activate the environment as shown above, then:
python -m pip install -r requirements.txt
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

The backend exposes `/healthz`, `/auth/login`, and `/auth/callback`, plus authenticated dashboard, library, and automation routes. API-triggered playlist actions can change the connected Spotify account; use them only after reviewing the selected action and target.

### Frontend

In a second terminal:

```bash
cd website/spotify-script-hub-main
npm ci
```

Create `.env.local` in that directory:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Then start Vite:

```bash
npm run dev
```

The site uses browser-based Spotify OAuth through the configured backend. Do not put Spotify client secrets or backend session secrets in `VITE_*` variables: those values are bundled into client-side code.

## Checks

Run the frontend tests and production build from its directory:

```bash
cd website/spotify-script-hub-main
npm test
npm run build
```

The repository's GitHub Actions workflows define CI checks and a GitHub Pages build/deploy workflow for pushes to `main`. The backend Dockerfile builds an API image, but backend deployment, database persistence, and environment secrets must be configured separately; a Pages workflow does not verify API health.

## Hosted endpoints — status as of 2026-11-04

- **Frontend:** [mayman20.github.io/spotify-playlist-tools](https://mayman20.github.io/spotify-playlist-tools) — deployed and live.
- **API:** [spotipy-website.onrender.com](https://spotipy-website.onrender.com) — currently unavailable while its database connection is checked.

The API may not support sign-in or dashboard data until service recovery is complete. Check current availability and OAuth configuration before relying on it.

## Optional monthly systemd runner

`deploy/spotify-monthly-recommend.timer` schedules the private monthly recommendation write for 09:00 America/New_York on the first day of each month. The service expects this checkout at `/opt/spotify-toolkit`, a Python environment at `.venv` with `deploy/requirements-monthly.txt` installed, and its restricted environment file at `/opt/spotify-toolkit/.env`. That file must include the Spotify app credentials and the exact `SPOTIFY_EXPECTED_USER_ID`; keep it mode `0600`. The service runs as `ubuntu` and uses a host-local Spotipy OAuth cache in `/opt/spotify-toolkit/.cache`. The runner is noninteractive and fails if its host-local cache or required scopes are missing. Provision authentication on the target host; do not copy an OAuth cache from another machine.

The runner uses a 20-minute subprocess limit and nonblocking lock. It stores mode-`0600` state and a private JSON outbox under `/var/lib/spotify-toolkit/monthly-runner`. It records a month as successful only after the CLI reports a confirmed write; if playlist privacy or the write result cannot be verified, it fails closed and does not record success. Empty recommendations, missing seeds, timeouts, and other errors remain failures eligible for a later retry. A repeated successful month is skipped. `--dry-run` previews without state or playlist writes, and `--status` prints sanitized local state. A repository checkout does not install or activate the timer; host provisioning and activation are separate deployment steps.

## Windows music visualizer

`music-visualizer/visualizer.py` captures Windows audio loopback and depends on Windows-specific audio support. It is not supported on macOS/Linux and does not use the API or web dashboard. On a supported Windows machine, install the visualizer's Python dependencies (`numpy`, `pygame`, and `pyaudiowpatch`) separately, then launch `music-visualizer/run.bat`.

## Privacy and repository hygiene

- Spotify tokens and application secrets belong in ignored `.env` files or a hosting provider's secret manager, never in source, frontend build variables, issues, or commits.
- OAuth cache files, local databases, logs, exports, and account-specific data should remain local and private.
- The backend encrypts stored OAuth tokens using a key derived from `APP_SECRET_KEY`; this does not protect source secrets or replace secure host/database configuration.
- Playlist scripts and authenticated API actions may read listening/library data and write playlist changes. Review requested OAuth scopes and the target playlist before granting access or running a write operation.
- Recommendation playlists are private by default in the monthly recommender's explicit write mode.

## Repository layout

```text
backend/                              FastAPI API, OAuth, token storage, task logic
scripts/                              Standalone Spotify workflows
website/spotify-script-hub-main/      React + TypeScript + Vite frontend
music-visualizer/                     Windows-only desktop visualizer
.github/workflows/                    Checks and GitHub Pages workflow
Dockerfile                             Backend container image
```

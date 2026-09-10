# Sobha Facilities Management Voice Agent

LiveKit voice assistant for Sobha Realty facilities management.

**Mic → Sarvam STT → OpenAI gpt-4o-mini → Sarvam TTS (ElevenLabs for Arabic) → speaker**

## Features

- Raise tickets (maintenance, repairs, complaints)
- Look up requests by ticket ID, unit, or resident name
- Missed-bus / transport dispatch
- Hindi, English, Malayalam, Arabic

## Local setup

1. Copy `.env.example` to `.env` and fill API keys.
2. Python 3.11 + the `sobha` conda env (or `pip install -r requirements.txt`).
3. Run:

```bash
bash run.sh
```

Open http://localhost:8765

## Deploy: Render (required)

The LiveKit **agent worker is a long-running process**. It cannot run on Vercel. Host it on [Render](https://render.com).

### Blueprint (recommended)

1. Push this repo to GitHub.
2. In Render: **New → Blueprint** → select the repo (`render.yaml`).
3. Fill env vars when prompted (same keys as `.env.example`):
   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
   - `OPENAI_API_KEY`, `SARVAM_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVEN_API_KEY`, `ELEVEN_VOICE_ID`
   - Web only: `CORS_ORIGINS` = your Vercel origin, e.g. `https://sobha-voice.vercel.app`
4. You get two services:
   - **sobha-voice-web** — FastAPI token API + playground (`SPAWN_AGENT=0`)
   - **sobha-voice-agent** — `python src/agent.py start` (always-on worker)

Use a **Starter** (or higher) plan so the worker does not sleep. A sleeping worker means calls connect with no agent.

After deploy, playground: `https://sobha-voice-web.onrender.com`

### Manual (no Blueprint)

| Service | Type | Build | Start |
|---|---|---|---|
| Web | Web | `pip install -r requirements.txt` | `uvicorn src.server:app --host 0.0.0.0 --port $PORT` |
| Agent | Background Worker | same | `python src/agent.py start` |

- Runtime: Python 3.11
- Region: Singapore (closest Render region to LiveKit India South)
- Web env: `SPAWN_AGENT=0`
- Same LiveKit/OpenAI/Sarvam/ElevenLabs keys on **both** services

## Deploy: Vercel (playground UI only)

Vercel only hosts the static playground. Token minting and the agent stay on Render.

1. Import the GitHub repo in [Vercel](https://vercel.com/new).
2. Framework Preset: **Other**. No build command.
3. Open `vercel.json` and set the rewrite destination to your Render **web** URL:

```json
{
  "rewrites": [
    { "source": "/", "destination": "/src/static/index.html" },
    {
      "source": "/api/:path*",
      "destination": "https://sobha-voice-web.onrender.com/api/:path*"
    }
  ]
}
```

4. Deploy. The browser still calls `/api/token` (same origin); Vercel proxies it to Render.
5. On Render web, set `CORS_ORIGINS` to `https://<your-app>.vercel.app` (and any custom domain).

Do **not** put `LIVEKIT_API_SECRET` or other secrets in Vercel. The static page never needs them.

### Alternative: playground on Render only

Skip Vercel. Use the Render web URL as the product URL.

## Architecture

```
Browser (Vercel or Render)
        │  GET /api/token
        ▼
FastAPI (Render web)
        │  LiveKit JWT + RoomAgentDispatch(sobha-agent)
        ▼
LiveKit Cloud room  ◄────  Agent worker (Render background worker)
                              Sarvam STT/TTS + OpenAI
```

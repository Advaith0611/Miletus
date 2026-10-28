# Miletus

Miletus turns PDF, DOCX, and Markdown study notes into a natural educational conversation between a host and an expert. `app.py` is kept untouched as the original Streamlit prototype; the production application is the small FastAPI API in `backend/` plus the static frontend in `frontend/`.

## Run it locally

Use Python 3.11 or newer. The two commands below run the complete app; open <http://localhost:5173> afterwards.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8000
```

In a **second terminal**, with the same virtual environment activated:

```bash
node frontend/build-config.mjs && python -m http.server 5173 --directory frontend
```

Set one LLM key in `.env`: `GEMINI_API_KEY`, `GROQ_API_KEY`, or `OPENROUTER_API_KEY`. For high-quality speech, set `ELEVENLABS_API_KEY` and both ElevenLabs voice IDs. Without a hosted voice provider, the Render Docker image falls back to eSpeak NG.

## Deploy

1. Push this repository to GitHub. Never commit `.env`.
2. In Render, create a new **Web Service** from the repository. Render detects `render.yaml` and uses the included Dockerfile. Copy the applicable values from `.env.example`, then set `FRONTEND_URL` to your Netlify URL, for example `https://your-site.netlify.app`.
3. In Netlify, import the same repository. It reads `netlify.toml`. Add a Netlify environment variable named `MILETUS_API_URL` whose value is your Render service URL, for example `https://miletus-api.onrender.com`. Do not add provider API keys to Netlify.
4. Redeploy Netlify after setting `MILETUS_API_URL`.

The API provides `GET /health`, `POST /api/generate`, `GET /api/jobs/{job_id}`, and `GET /api/jobs/{job_id}/audio`. Generated audio is temporary, which suits this no-account app and Render's ephemeral filesystem.

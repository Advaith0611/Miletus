 # Miletus

Miletus turns one or many documents into a detailed educational podcast: upload notes, chapters, papers, or articles and receive a two-voice conversation between a teacher and a student. It has no accounts, dashboard, or database.

## Architecture

```text
Netlify (static HTML/CSS/JS) → FastAPI on Render → Groq / OpenRouter → ElevenLabs → FFmpeg → MP3
```

The backend is Python-only. A job accepts repeated `files` fields (up to `MAX_FILES_PER_JOB`) and combines their source-aware sections before analysis. Jobs are kept in memory and temporary files live under an isolated directory. Render's filesystem is ephemeral, so generated files are intentionally short-lived; this is appropriate for the no-account MVP.

## Local setup

Python 3.11+ and FFmpeg are recommended. On macOS: `brew install ffmpeg`. On Ubuntu: `sudo apt-get install ffmpeg`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
cp .env.example .env
```

To run without paid APIs, set `MOCK_PROVIDERS=true` in `.env`. Mock mode creates a short local WAV fixture for every dialogue turn; it exercises the complete upload, job, script, audio, and download flow but is not production audio.

Start the API:

```bash
uvicorn main:app --reload --port 8000
```

Serve the static frontend in another terminal:

```bash
python -m http.server 5173 --directory frontend
```

Open <http://localhost:5173>. The frontend defaults to `http://localhost:8000`; set `window.MILETUS_API_URL` before loading `app.js` if the API is elsewhere.

## Environment variables

See `.env.example`. Configure at least one of `GROQ_API_KEY` or `OPENROUTER_API_KEY`, plus both ElevenLabs voice IDs, for production. Groq is attempted first and OpenRouter is used as a fallback after provider errors. Models are configurable and are not hard-coded into the pipeline.

## API

- `GET /health` — Render health check.
- `POST /api/generate` — multipart form upload; send one or more fields named `files`.
- `GET /api/jobs/{job_id}` — progress and completion metadata.
- `GET /api/jobs/{job_id}/audio` — generated MP3.
- `GET /api/jobs/{job_id}/transcript` — plain-text transcript.

Generation runs in an asyncio background task. The stages are extraction, analysis, writing, validation, audio generation, and assembly. Long documents are section- and paragraph-chunked, and the LLM receives a bounded source window. Intermediate chunks and script JSON are cached in the job directory so a TTS failure does not require script generation again.

## Deployment

### 0. Put the project on GitHub first

Create an empty GitHub repository, then from this project directory run:

```bash
git init
git branch -M main
git add .
git commit -m "Build Miletus document to podcast app"
git remote add origin https://github.com/YOUR_USERNAME/miletus.git
git push -u origin main
```

`.gitignore` excludes `.env`; never commit provider keys. The repository must contain `Dockerfile`, `render.yaml`, `netlify.toml`, `frontend/`, and `backend/`.

### Render

Create a Web Service from the GitHub repository. Render will use `render.yaml` and the Dockerfile; FFmpeg is installed in the image. Add the variables from `.env.example` in Render's Environment settings, set `MOCK_PROVIDERS=false`, and set `FRONTEND_URL` to the exact Netlify site origin. The service URL will look like `https://miletus-api.onrender.com`.

### Netlify

Import the GitHub repository as a new site. Netlify will use `netlify.toml`, publish `frontend`, and run the small build script. Add the environment variable `MILETUS_API_URL` with the Render URL, for example `https://miletus-api.onrender.com`. Do not put provider keys in Netlify.

In Netlify's Domain management, add `miletus.pythios.xyz`. At your domain registrar's DNS settings, create a CNAME record with host `miletus` pointing to the Netlify site's generated `YOUR-SITE.netlify.app` hostname. Netlify will issue HTTPS automatically after DNS verification.

## Tests

```bash
pytest -q
```

The tests cover Markdown extraction, chunk limits and metadata, structured script validation, JSON repair parsing, multi-file upload, health, unsupported extensions, mock job completion, audio delivery, and transcript delivery.

## Troubleshooting

- If a job fails immediately in production, verify at least one LLM key and both ElevenLabs voice IDs.
- If audio assembly fails, install FFmpeg and ensure `ffmpeg` is on `PATH`.
- If the browser cannot upload, check `FRONTEND_URL` and that it matches the Netlify origin exactly.
- Temporary files are cleaned on startup according to `TEMP_FILE_RETENTION_MINUTES`.

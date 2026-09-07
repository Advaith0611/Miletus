"""Write the static frontend's runtime API URL from Netlify build variables."""

import json
import os
from pathlib import Path

api_url = os.getenv("MILETUS_API_URL", "http://localhost:8000").rstrip("/")
Path("frontend/config.js").write_text(
    f"window.MILETUS_API_URL = {json.dumps(api_url)};\n",
    encoding="utf-8",
)

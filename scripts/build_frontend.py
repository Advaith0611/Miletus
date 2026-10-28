"""Write the public API URL into the static site at Netlify build time."""

from __future__ import annotations

import json
import os
from pathlib import Path

api_url = os.getenv("MILETUS_API_URL", "http://localhost:8000").rstrip("/")
Path("frontend/config.js").write_text(
    f"window.MILETUS_API_URL = {json.dumps(api_url)};\n", encoding="utf-8"
)

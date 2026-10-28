import { writeFileSync } from "node:fs";

const apiUrl = (process.env.MILETUS_API_URL || "http://localhost:8000").replace(/\/$/, "");
writeFileSync(new URL("./config.js", import.meta.url), `window.MILETUS_API_URL = ${JSON.stringify(apiUrl)};\n`);

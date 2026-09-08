from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str, fallback: str) -> str:
    path = PROMPT_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else fallback


def render_prompt(template: str, **values: str) -> str:
    """Replace only our named template variables; JSON braces stay literal."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


ANALYSIS_PROMPT = load_prompt("document_analysis.txt", "You are a careful source analyst. Return JSON with subject, title, major_topics, key_concepts, terminology, relationships, examples, misconceptions, and uncertainties. Ground every item in the supplied source; mark missing or unclear material as uncertain.\n\nSOURCE:\n{source}")
GENERATION_PROMPT = load_prompt("podcast_generation.txt", "You are an expert educator and podcast writer. Transform the supplied source material and analysis into a detailed, natural conversation between a teacher and a student. Teach rather than merely summarize. Return only valid JSON matching the requested podcast schema.\n\nTarget duration: {target_duration} minutes (about {target_words} spoken words).\n\nANALYSIS:\n{analysis}\n\nSOURCE:\n{source}")

"""Gemini transport for resume tailoring.

The only module that talks to the Gemini API. Everything here returns None on
failure and logs the reason — resume tailoring is best-effort and must never
break the Apply flow that triggered it.

Requires `google-genai`, imported lazily so the rest of the project works
without it installed.
"""

from typing import Optional

from common.logger import get_logger
from common.resume import config

logger = get_logger("resume.gemini")

# Low temperature: this is a rewriting task, not a creative one. Higher values
# make the model more likely to embellish, which is exactly the failure mode
# the prompt is trying to prevent.
_TEMPERATURE = 0.2
_MAX_OUTPUT_TOKENS = 8192


def _build_client():
    """Construct a Gemini client, or None if the SDK is missing or unusable."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("google-genai is not installed — run: pip install google-genai")
        return None

    timeout_ms = config.GEMINI_TIMEOUT_SECONDS * 1000
    try:
        return genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
    except TypeError:
        # Older SDK versions do not accept http_options — fall back to defaults.
        logger.debug("SDK does not support http_options timeout; using defaults")
        try:
            return genai.Client(api_key=config.GEMINI_API_KEY)
        except Exception:
            logger.exception("Failed to construct Gemini client")
            return None
    except Exception:
        logger.exception("Failed to construct Gemini client")
        return None


def strip_code_fences(text: str) -> str:
    """
    Remove markdown fences the model may wrap around the LaTeX despite being
    told not to. Handles ```latex, ```tex, and bare ```.
    """
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned

    # Drop the opening fence line (```latex / ```tex / ```)
    lines = cleaned.splitlines()[1:]
    # Drop the closing fence if the model included one
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def generate(prompt: str) -> Optional[str]:
    """
    Send the tailoring prompt to Gemini and return the LaTeX source it produced.
    Returns None on any failure.
    """
    client = _build_client()
    if not client:
        return None

    try:
        from google.genai import types
        gen_config = types.GenerateContentConfig(
            temperature=_TEMPERATURE,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
    except Exception:
        gen_config = None

    logger.info("Requesting tailored resume from %s (prompt %d chars)",
                config.GEMINI_MODEL, len(prompt))
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config=gen_config,
        )
    except Exception:
        logger.exception("Gemini request failed")
        return None

    text = getattr(response, "text", None)
    if not text or not text.strip():
        # A blank .text usually means the response was cut short or filtered.
        logger.error("Gemini returned an empty response (feedback=%s)",
                     getattr(response, "prompt_feedback", None))
        return None

    latex = strip_code_fences(text)
    logger.info("Gemini returned %d chars of LaTeX", len(latex))
    return latex

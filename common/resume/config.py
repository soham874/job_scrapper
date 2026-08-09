"""Configuration for the resume tailoring module.

Two independent switches:

  RESUME_ENABLED         master switch for the whole feature. Off means the
                         Apply flow behaves exactly as it did before.
  RESUME_GEMINI_ENABLED  whether Gemini is actually called. Off still produces
                         a PDF — from the untouched base resume — so the LaTeX
                         and Telegram halves can be exercised without spending
                         API calls.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from common.logger import get_logger

logger = get_logger("resume.config")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODULE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _path(name: str, default: Path) -> Path:
    """Resolve an env-configured path; relative values resolve from the repo root."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else BASE_DIR / p


RESUME_ENABLED = _flag("RESUME_ENABLED")
RESUME_GEMINI_ENABLED = _flag("RESUME_GEMINI_ENABLED")
# Strip every region marked with RESUME_REDACT_START/END in the .tex file before
# sending the resume to Gemini, and splice the originals back afterwards.
RESUME_REDACT_PERSONAL = _flag("RESUME_REDACT_PERSONAL", default="true")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# Flash-class models are the ones on the AI Studio free tier.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "120"))

PROMPT_FILE = _path("RESUME_PROMPT_FILE", MODULE_DIR / "prompt.md")
BASE_RESUME_FILE = _path("RESUME_BASE_FILE", MODULE_DIR / "base_resume.tex")
OUTPUT_DIR = _path("RESUME_OUTPUT_DIR", BASE_DIR / "generated_resumes")

# Optional explicit engine ("tectonic", "xelatex", "lualatex", "pdflatex").
# Empty means autodetect.
LATEX_ENGINE = os.getenv("LATEX_ENGINE", "").strip()

# Guard against a model that ignores instructions and returns something wildly
# different from the base resume. Ratios are output length / base length.
MIN_OUTPUT_RATIO = 0.5
MAX_OUTPUT_RATIO = 2.0


def gemini_available() -> bool:
    """True when Gemini is switched on and has an API key."""
    if not RESUME_GEMINI_ENABLED:
        return False
    if not GEMINI_API_KEY:
        logger.warning("RESUME_GEMINI_ENABLED is on but GEMINI_API_KEY is empty — "
                       "falling back to the untailored base resume")
        return False
    return True


def describe() -> str:
    """One-line summary of the active configuration, for logs and the test script."""
    return (
        f"enabled={RESUME_ENABLED} gemini={RESUME_GEMINI_ENABLED} "
        f"key={'set' if GEMINI_API_KEY else 'missing'} model={GEMINI_MODEL} "
        f"engine={LATEX_ENGINE or 'autodetect'}"
    )

"""Resume tailoring orchestration.

Flow: load base LaTeX -> build prompt -> call Gemini -> validate -> compile PDF.

Every entry point returns None rather than raising. This runs off the back of a
Telegram Apply click; a tailoring failure should cost the user a resume, not the
application-tracking that click also performs.
"""

import html
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from common.logger import get_logger
from common.resume import config, gemini
from common.resume.latex import compile_pdf

logger = get_logger("resume.service")

_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# Block-level tags become line breaks; everything else is inline and must not
# split a sentence. Job descriptions are mostly <ul>/<li>, so getting this right
# is the difference between readable requirements and shredded fragments.
_BLOCK_TAGS = re.compile(
    r"(?i)</?(p|div|br|li|ul|ol|h[1-6]|tr|table|section|article|header|footer)\b[^>]*>"
)

_REDACT_START = "% RESUME_REDACT_START"
_REDACT_END = "% RESUME_REDACT_END"

# Any number of marked regions, anywhere in the file — a resume typically has
# personal data in more than one place (contact header, PDF metadata in the
# preamble, a personal site URL in a project bullet).
_REDACT_BLOCK = re.compile(
    re.escape(_REDACT_START) + r".*?" + re.escape(_REDACT_END),
    re.DOTALL,
)


def _placeholder(index: int) -> str:
    """Stand-in sent to the model in place of one redacted region."""
    return (
        f"{_REDACT_START}\n"
        f"% [Personal details withheld. Reproduce these lines exactly as-is,\n"
        f"%  including both comment markers. Do not invent a name, contact\n"
        f"%  details, or links to replace them.]\n"
        f"REDACTED_PERSONAL_BLOCK_{index}\n"
        f"{_REDACT_END}"
    )

# Job descriptions can be enormous. Trim to keep free-tier requests small; the
# useful signal (responsibilities, requirements) is near the top.
_MAX_DESCRIPTION_CHARS = 12000

# Below this, treat the description as a stub rather than real posting text —
# some ATS feeds return only a one-line teaser.
_MIN_USEFUL_DESCRIPTION_CHARS = 200


def html_to_text(raw: str) -> str:
    """
    Convert a scraped job description to plain text, preserving case.

    common.analyzer has a similar helper, but it lowercases for keyword matching.
    Casing matters here — it goes into a prompt a human will read the output of.
    """
    if not raw:
        return ""
    text = html.unescape(raw)
    if "<" in text and ">" in text:
        # Turn block boundaries into newlines first, then let BeautifulSoup strip
        # the remaining inline tags with a space so words stay joined up.
        text = _BLOCK_TAGS.sub("\n", text)
        text = BeautifulSoup(text, "html.parser").get_text(separator=" ")
    text = unicodedata.normalize("NFKC", text)
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_LINES.sub("\n\n", text).strip()


def _slugify(value: str, fallback: str = "job") -> str:
    """Filesystem-safe fragment for the output filename."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (value or "")).strip("-").lower()
    return slug[:40] or fallback


def _read(path: Path, label: str) -> Optional[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("%s not found at %s", label, path)
        return None
    except Exception:
        logger.exception("Failed to read %s at %s", label, path)
        return None
    if not content.strip():
        logger.error("%s at %s is empty", label, path)
        return None
    return content


def load_base_resume() -> Optional[str]:
    """Read the configured base resume. Returns None if missing or empty."""
    return _read(config.BASE_RESUME_FILE, "Base resume")


def redact_personal(tex: str) -> tuple:
    """
    Replace every marked region with a numbered placeholder.

    Returns (regions, redacted_tex) where regions holds the original text of each
    region in document order. An empty regions list means no markers were found
    and the resume would be sent whole.
    """
    regions = []

    def _swap(match) -> str:
        regions.append(match.group(0))
        return _placeholder(len(regions))

    # re.sub with a function treats the return value literally, so the backslashes
    # in the placeholder need no escaping.
    redacted = _REDACT_BLOCK.sub(_swap, tex)
    return regions, redacted


def restore_personal(tex: str, regions: list) -> Optional[str]:
    """
    Put the real regions back, in order.

    Returns None if the model did not return exactly as many marked regions as
    were sent. That mismatch means we cannot map placeholders back to originals
    with confidence, and a resume carrying the wrong contact details — or none —
    is worse than an untailored one, so the caller falls back to the base resume.
    """
    found = _REDACT_BLOCK.findall(tex)
    if len(found) != len(regions):
        logger.error("Redaction markers mismatch: sent %d region(s), model returned %d. "
                     "Cannot restore personal details safely.", len(regions), len(found))
        return None

    remaining = iter(regions)
    return _REDACT_BLOCK.sub(lambda _: next(remaining), tex)


def build_payload(title: str, company: str, location: str,
                  description: str) -> Optional[str]:
    """
    Return the exact text that would be sent to Gemini, for inspection.

    Used by the test script's --show-payload so you can confirm what leaves the
    machine before enabling the feature.
    """
    base = load_base_resume()
    if not base:
        return None
    _, redacted = (redact_personal(base) if config.RESUME_REDACT_PERSONAL
                   else ([], base))
    return build_prompt(title, company, location, description, redacted)


def describe_posting(description: str, keywords: Optional[list] = None) -> str:
    """
    Build the posting context for the prompt.

    The full description is preferred, but it is not always there: rows predating
    migration V012 have none, some ATSes never expose one, and partial feeds
    return a stub. In those cases the analyzer's matched keywords are the best
    remaining signal — they are what got the job past the score threshold in the
    first place, so they describe what the posting actually asks for.
    """
    description = html_to_text(description)
    if len(description) > _MAX_DESCRIPTION_CHARS:
        logger.info("Truncating job description from %d to %d chars",
                    len(description), _MAX_DESCRIPTION_CHARS)
        description = description[:_MAX_DESCRIPTION_CHARS]

    # A handful of characters is a stub, not a description.
    if len(description) >= _MIN_USEFUL_DESCRIPTION_CHARS:
        return description

    if keywords:
        logger.info("Description missing or too short (%d chars) — falling back to "
                    "%d analyzer keyword(s)", len(description), len(keywords))
        listed = ", ".join(str(k) for k in keywords)
        context = (
            "(The full posting text is not available. What follows is the list of "
            "technical topics detected in this posting by keyword analysis. Treat "
            "each as something the posting asks for, and tailor accordingly — but "
            "the rules above still hold: surface only what the base resume already "
            "contains, and never add a skill to match a keyword.)\n\n"
            f"Topics detected in the posting: {listed}"
        )
        if description:
            context += f"\n\nPartial description text:\n{description}"
        return context

    logger.warning("No description and no keywords — tailoring on title/company alone")
    return ("(No description text or keyword analysis is available for this posting. "
            "Tailor using only the job title and company above, and make minimal "
            "changes — with this little signal, leaving the resume close to the "
            "base is the correct outcome.)")


def has_tailoring_signal(description: str, keywords: Optional[list] = None) -> bool:
    """
    True when there is enough posting context for tailoring to beat the base resume.

    Used to skip the API call entirely rather than spend a request producing a
    near-no-op — which matters on a rate-limited free tier.
    """
    if len(html_to_text(description)) >= _MIN_USEFUL_DESCRIPTION_CHARS:
        return True
    return bool(keywords)


def build_prompt(title: str, company: str, location: str,
                 description: str, base_resume: str,
                 keywords: Optional[list] = None) -> Optional[str]:
    """Fill the prompt template. Returns None if the template is unreadable."""
    template = _read(config.PROMPT_FILE, "Prompt template")
    if not template:
        return None

    description = describe_posting(description, keywords)

    return (
        template
        .replace("<<JOB_TITLE>>", title or "Unknown")
        .replace("<<COMPANY>>", company or "Unknown")
        .replace("<<LOCATION>>", location or "Unspecified")
        .replace("<<JOB_DESCRIPTION>>", description)
        .replace("<<BASE_RESUME>>", base_resume)
    )


def validate_latex(candidate: str, base: str) -> bool:
    """
    Cheap structural checks before spending a LaTeX compile on the output.

    This catches a model that returned prose, a truncated document, or something
    wildly different from the base. It does NOT catch the failure mode that
    matters most — an inflated verb or an invented metric — which is why the
    generated PDF is worth reading before you send it anywhere.
    """
    if not candidate or not candidate.strip():
        logger.error("Validation failed: empty output")
        return False

    if "\\documentclass" not in candidate:
        logger.error("Validation failed: no \\documentclass in output")
        return False

    if "\\end{document}" not in candidate:
        logger.error("Validation failed: no \\end{document} — output likely truncated "
                     "(try raising max output tokens or shortening the base resume)")
        return False

    ratio = len(candidate) / max(len(base), 1)
    if not (config.MIN_OUTPUT_RATIO <= ratio <= config.MAX_OUTPUT_RATIO):
        logger.error("Validation failed: output is %.2fx the base resume length "
                     "(allowed %.2f-%.2f)", ratio, config.MIN_OUTPUT_RATIO,
                     config.MAX_OUTPUT_RATIO)
        return False

    logger.debug("Validation passed (%.2fx base length)", ratio)
    return True


def tailor_latex(title: str, company: str, location: str,
                 description: str, base_resume: str,
                 keywords: Optional[list] = None) -> str:
    """
    Return tailored LaTeX, or the untouched base resume if tailoring is off or
    fails. Always returns compilable source.
    """
    if not config.gemini_available():
        logger.info("Gemini disabled — using base resume unmodified")
        return base_resume

    if not has_tailoring_signal(description, keywords):
        logger.warning("No usable posting signal for '%s' at '%s' — skipping the "
                       "Gemini call and sending the base resume", title, company)
        return base_resume

    regions, prompt_resume = (redact_personal(base_resume)
                              if config.RESUME_REDACT_PERSONAL else ([], base_resume))
    if config.RESUME_REDACT_PERSONAL:
        if regions:
            logger.info("Redacted %d personal region(s) from the payload", len(regions))
        else:
            logger.warning(
                "RESUME_REDACT_PERSONAL is on but no %s/%s marker pairs found in %s — "
                "the resume will be sent to Gemini in full, personal details included",
                _REDACT_START, _REDACT_END, config.BASE_RESUME_FILE,
            )

    prompt = build_prompt(title, company, location, description, prompt_resume, keywords)
    if not prompt:
        return base_resume

    tailored = gemini.generate(prompt)
    if not tailored:
        logger.warning("Tailoring failed — falling back to base resume")
        return base_resume

    if not validate_latex(tailored, prompt_resume):
        logger.warning("Tailored output failed validation — falling back to base resume")
        return base_resume

    if regions:
        restored = restore_personal(tailored, regions)
        if restored is None:
            logger.warning("Could not restore personal details — falling back to base resume")
            return base_resume
        tailored = restored

    return tailored


def generate_resume(title: str, company: str, location: str = "",
                    description: str = "", job_id: Optional[int] = None,
                    keywords: Optional[list] = None) -> Optional[Path]:
    """
    Produce a tailored resume PDF for one posting.

    keywords is the analyzer's positive_matches list, used as the tailoring
    signal when the description is unavailable.

    Returns the PDF path, or None if the resume could not be produced at all
    (missing base file, or no LaTeX toolchain).
    """
    base_resume = load_base_resume()
    if not base_resume:
        return None

    tailored = tailor_latex(title, company, location, description, base_resume, keywords)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = f"{job_id}-" if job_id else ""
    filename = f"{prefix}{_slugify(company, 'company')}-{_slugify(title, 'role')}-{stamp}.pdf"
    output_path = config.OUTPUT_DIR / filename

    return compile_pdf(tailored, output_path)


def generate_resume_for_job(job: dict, description: str = "",
                            keywords: Optional[list] = None) -> Optional[Path]:
    """Convenience wrapper taking the job dict shape returned by get_job_by_id()."""
    return generate_resume(
        title=job.get("title", ""),
        company=job.get("company", ""),
        location=job.get("location", ""),
        description=description,
        job_id=job.get("id"),
        keywords=keywords,
    )

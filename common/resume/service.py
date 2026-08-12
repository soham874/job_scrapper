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

_DYNAMIC_START = "% RESUME_DYNAMIC_START"
_DYNAMIC_END = "% RESUME_DYNAMIC_END"

# One tailorable region. The name in parentheses is what maps a returned
# fragment back to the place it came from, so the model may return regions in
# any order — or omit one — without anything landing in the wrong section.
_DYNAMIC_BLOCK = re.compile(
    re.escape(_DYNAMIC_START) + r"\((?P<name>[A-Za-z0-9_]+)\)\n"
    r"(?P<body>.*?)\n"
    + re.escape(_DYNAMIC_END),
    re.DOTALL,
)

# Scaffolding that must never appear inside a fragment. Seeing any of it means
# the model ignored the contract and returned a whole document, in which case
# splicing it into a region would nest a document inside itself.
_SCAFFOLDING = ("\\documentclass", "\\usepackage",
                "\\begin{document}", "\\end{document}")

# Escaped braces are literal characters, not grouping, so they must not count
# towards the balance check.
_ESCAPED_BRACE = re.compile(r"\\[{}]")

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


def split_dynamic(tex: str) -> dict:
    """
    Return {region name: body} for every marked region, in document order.

    Used on both sides of the call: on the base resume to learn what may be
    tailored, and on the response to read back what the model produced.
    """
    return {m.group("name"): m.group("body") for m in _DYNAMIC_BLOCK.finditer(tex)}


def build_context(tex: str) -> str:
    """
    The slice of the resume the model is allowed to see.

    Everything from the first \\section to \\end{document} — the tailorable
    regions plus the static sections around them, which the model needs in order
    to judge what is relevant. Deliberately excludes two things: the preamble,
    which cannot be corrupted if it is never sent, and the contact header above
    the first section, which is the only place personal details live. Leaving
    the header out of the payload is what makes a separate redaction step
    unnecessary rather than merely correct.
    """
    start = tex.find("\\section{")
    end = tex.find("\\end{document}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return tex[start:end].strip()


def apply_fragments(tex: str, fragments: dict) -> str:
    """
    Splice tailored bodies into their marked regions, leaving everything else
    byte-for-byte unchanged. A region with no accepted fragment keeps its
    original body.
    """
    def _swap(match) -> str:
        name = match.group("name")
        body = fragments.get(name)
        if body is None:
            return match.group(0)
        return f"{_DYNAMIC_START}({name})\n{body}\n{_DYNAMIC_END}"

    # re.sub with a function treats the return value literally, so the
    # backslashes throughout the LaTeX body need no escaping.
    return _DYNAMIC_BLOCK.sub(_swap, tex)


def validate_fragment(name: str, candidate: str, original: str) -> bool:
    """
    Cheap structural checks on one returned region before it is spliced in.

    These catch a mangled fragment, not a dishonest one — an inflated verb or an
    invented metric passes every check here, which is why the generated PDF is
    still worth reading before you send it anywhere.
    """
    if not candidate or not candidate.strip():
        logger.error("Fragment '%s': empty", name)
        return False

    leaked = [t for t in _SCAFFOLDING if t in candidate]
    if leaked:
        logger.error("Fragment '%s': contains %s — the model returned document "
                     "scaffolding instead of just the region body",
                     name, ", ".join(leaked))
        return False

    bare = _ESCAPED_BRACE.sub("", candidate)
    if bare.count("{") != bare.count("}"):
        logger.error("Fragment '%s': unbalanced braces (%d open, %d close) — "
                     "this would fail the compile",
                     name, bare.count("{"), bare.count("}"))
        return False

    ratio = len(candidate) / max(len(original), 1)
    if not (config.MIN_OUTPUT_RATIO <= ratio <= config.MAX_OUTPUT_RATIO):
        logger.error("Fragment '%s': %.2fx the original length (allowed %.2f-%.2f)",
                     name, ratio, config.MIN_OUTPUT_RATIO, config.MAX_OUTPUT_RATIO)
        return False

    return True


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
    return build_prompt(title, company, location, description, base)


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

    body = build_context(base_resume)
    if not body:
        logger.error("Could not locate the section body in %s — expected a "
                     "\\section{...} somewhere before \\end{document}",
                     config.BASE_RESUME_FILE)
        return None

    description = describe_posting(description, keywords)

    return (
        template
        .replace("<<JOB_TITLE>>", title or "Unknown")
        .replace("<<COMPANY>>", company or "Unknown")
        .replace("<<LOCATION>>", location or "Unspecified")
        .replace("<<JOB_DESCRIPTION>>", description)
        .replace("<<RESUME_BODY>>", body)
    )


def tailor_latex(title: str, company: str, location: str,
                 description: str, base_resume: str,
                 keywords: Optional[list] = None) -> str:
    """
    Return tailored LaTeX, or the untouched base resume if tailoring is off or
    fails. Always returns compilable source.

    Only the marked regions are ever replaced. The preamble, the contact header
    and the static sections carry through from the base resume byte-for-byte, so
    a bad response can cost tailoring but cannot break the document.
    """
    if not config.gemini_available():
        logger.info("Gemini disabled — using base resume unmodified")
        return base_resume

    if not has_tailoring_signal(description, keywords):
        logger.warning("No usable posting signal for '%s' at '%s' — skipping the "
                       "Gemini call and sending the base resume", title, company)
        return base_resume

    originals = split_dynamic(base_resume)
    if not originals:
        logger.warning("No %s(...) regions found in %s — nothing to tailor, "
                       "sending the base resume unmodified",
                       _DYNAMIC_START, config.BASE_RESUME_FILE)
        return base_resume

    prompt = build_prompt(title, company, location, description, base_resume, keywords)
    if not prompt:
        return base_resume

    raw = gemini.generate(prompt)
    if not raw:
        logger.warning("Tailoring failed — falling back to base resume")
        return base_resume

    returned = split_dynamic(raw)
    if not returned:
        logger.warning("Model returned no %s(...) regions — falling back to base "
                       "resume", _DYNAMIC_START)
        return base_resume

    unexpected = set(returned) - set(originals)
    if unexpected:
        logger.warning("Ignoring %d returned region(s) the base resume does not "
                       "define: %s", len(unexpected), ", ".join(sorted(unexpected)))

    # Per-region fallback rather than all-or-nothing: one mangled fragment costs
    # its own section, not the tailoring of every other section.
    accepted = {}
    for name, original in originals.items():
        candidate = returned.get(name)
        if candidate is None:
            logger.warning("Fragment '%s' missing from the response — keeping the "
                           "base version", name)
            continue
        if not validate_fragment(name, candidate, original):
            logger.warning("Fragment '%s' failed validation — keeping the base "
                           "version", name)
            continue
        accepted[name] = candidate

    if not accepted:
        logger.warning("No fragment survived validation — falling back to base resume")
        return base_resume

    logger.info("Tailored %d of %d region(s): %s",
                len(accepted), len(originals), ", ".join(accepted))
    return apply_fragments(base_resume, accepted)


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

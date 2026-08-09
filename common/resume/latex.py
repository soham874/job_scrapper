"""LaTeX to PDF compilation.

Engine-agnostic: prefers tectonic (self-contained, fetches its own packages),
then falls back to the usual TeX Live engines. Compilation happens in a temp
directory so a failed run leaves no .aux/.log litter next to the output.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from common.logger import get_logger
from common.resume import config

logger = get_logger("resume.latex")

# Ordered by preference. pdflatex is first because the common resume templates
# (Jake Gutierrez / sb2nov and friends) use \pdfgentounicode, a pdfTeX primitive
# that makes the PDF text extractable by ATS parsers. XeTeX-based engines —
# including tectonic — do not provide it and fail on those templates.
_ENGINE_PREFERENCE = ("pdflatex", "lualatex", "xelatex", "tectonic")

# Primitives that only exist under pdfTeX. Presence of any of these means the
# document needs pdflatex (or lualatex, which aliases most of them).
_PDFTEX_ONLY = ("\\pdfgentounicode", "\\pdfglyphtounicode", "\\pdfcompresslevel")
_XETEX_ENGINES = ("xelatex", "tectonic")

# Generous because tectonic downloads its package bundle on first use — a cold
# run can take several minutes where a warm one takes ~10s. Generation happens on
# a background thread, so a long ceiling costs nothing.
_COMPILE_TIMEOUT_SECONDS = 420


def find_engine() -> Optional[str]:
    """Return the LaTeX engine to use, or None if no toolchain is installed."""
    if config.LATEX_ENGINE:
        if shutil.which(config.LATEX_ENGINE):
            return config.LATEX_ENGINE
        logger.error("LATEX_ENGINE=%s is set but that binary is not on PATH",
                     config.LATEX_ENGINE)
        return None

    for engine in _ENGINE_PREFERENCE:
        if shutil.which(engine):
            return engine
    return None


def check_engine_compatibility(tex_source: str, engine: str) -> Optional[str]:
    """
    Return a warning string if the engine cannot compile this source, else None.

    Catches the common case up front: a resume template using \\pdfgentounicode
    (which is what makes the PDF readable by ATS parsers) handed to a XeTeX-based
    engine, which fails with an unhelpful "undefined control sequence".
    """
    if engine not in _XETEX_ENGINES:
        return None
    found = [p for p in _PDFTEX_ONLY if p in tex_source]
    if not found:
        return None

    # A template that wraps them in \ifdefined compiles fine everywhere: pdfTeX
    # runs the block, XeTeX engines skip it. Nothing to warn about.
    if all(f"\\ifdefined{p}" in tex_source for p in found):
        logger.debug("pdfTeX primitives are \\ifdefined-guarded — %s can compile this", engine)
        return None

    msg = (f"{engine} is XeTeX-based and does not support {', '.join(found)}. "
           f"This resume needs pdflatex. ")
    if shutil.which("pdflatex"):
        msg += "pdflatex is installed — set LATEX_ENGINE=pdflatex in .env."
    else:
        msg += ("pdflatex is NOT installed — install it first: "
                "brew install --cask mactex-no-gui")
    msg += (" Removing the offending lines from the template would also work, but "
            "they are what makes the PDF text extractable by ATS parsers, so that "
            "is a real loss.")
    return msg


def _build_command(engine: str, tex_path: Path, workdir: Path) -> list:
    if engine == "tectonic":
        return [engine, "--outdir", str(workdir), "--keep-logs", str(tex_path)]
    return [
        engine,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-output-directory", str(workdir),
        str(tex_path),
    ]


def _log_tail(workdir: Path, stem: str, limit: int = 40) -> str:
    """Return the tail of the LaTeX log, which is where the real error lives."""
    log_file = workdir / f"{stem}.log"
    if not log_file.exists():
        return "(no log file produced)"
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return "(log file unreadable)"
    return "\n".join(lines[-limit:])


def compile_pdf(tex_source: str, output_path: Path) -> Optional[Path]:
    """
    Compile LaTeX source to a PDF at output_path.

    Returns the output path on success, None on failure. Never raises — a
    tailoring failure must not take down the caller.
    """
    engine = find_engine()
    if not engine:
        logger.error(
            "No LaTeX engine found. Install one of: %s. "
            "On macOS: `brew install tectonic` (lightest) or the full MacTeX.",
            ", ".join(_ENGINE_PREFERENCE),
        )
        return None

    incompatible = check_engine_compatibility(tex_source, engine)
    if incompatible:
        logger.error("%s", incompatible)
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="resume-latex-") as tmp:
        workdir = Path(tmp)
        stem = output_path.stem
        tex_path = workdir / f"{stem}.tex"
        tex_path.write_text(tex_source, encoding="utf-8")

        # TeX engines need two passes to settle references and page numbering.
        # tectonic handles that internally, so one invocation is enough.
        passes = 1 if engine == "tectonic" else 2
        for attempt in range(1, passes + 1):
            try:
                result = subprocess.run(
                    _build_command(engine, tex_path, workdir),
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=_COMPILE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                logger.error("%s timed out after %ds.%s", engine, _COMPILE_TIMEOUT_SECONDS,
                             " On a first run this is usually the package-bundle "
                             "download — run it once by hand to warm the cache."
                             if engine == "tectonic" else "")
                return None
            except Exception:
                logger.exception("Failed to invoke %s", engine)
                return None

            if result.returncode != 0:
                logger.error("%s failed on pass %d (exit %d). Log tail:\n%s",
                             engine, attempt, result.returncode, _log_tail(workdir, stem))
                return None

        produced = workdir / f"{stem}.pdf"
        if not produced.exists():
            logger.error("%s reported success but produced no PDF. Log tail:\n%s",
                         engine, _log_tail(workdir, stem))
            return None

        shutil.copyfile(produced, output_path)

    logger.info("Compiled resume PDF via %s -> %s (%d bytes)",
                engine, output_path, output_path.stat().st_size)
    return output_path

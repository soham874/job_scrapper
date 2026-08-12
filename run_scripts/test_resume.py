"""Exercise the resume module on its own — no database, no scraping, no webhook.

Examples
--------
Diagnostics only (config, LaTeX engine, file paths):
    python3 run_scripts/test_resume.py --check

Generate with the built-in sample posting:
    python3 run_scripts/test_resume.py

Generate from a real posting saved to a file:
    python3 run_scripts/test_resume.py --title "Senior Backend Engineer" \\
        --company "Acme" --location Bangalore --jd /tmp/posting.txt

See exactly what would be sent to Gemini, without calling it — run this after
editing your resume to confirm no personal detail slips through:
    python3 run_scripts/test_resume.py --show-payload

Inspect the LaTeX Gemini produced without compiling it (prompt debugging):
    python3 run_scripts/test_resume.py --jd /tmp/posting.txt --dump-latex out.tex

Also push the PDF to Telegram, to test delivery:
    python3 run_scripts/test_resume.py --send
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.resume import config  # noqa: E402
from common.resume.latex import check_engine_compatibility, find_engine  # noqa: E402
from common.resume.service import (  # noqa: E402
    build_payload,
    generate_resume,
    load_base_resume,
    split_dynamic,
    tailor_latex,
)

SAMPLE_JD = """
We are looking for a Senior Backend Engineer to join our platform team in
Bangalore.

Responsibilities:
- Design and build distributed microservices handling high request volumes
- Own service reliability, including caching, load balancing and fault tolerance
- Work with Kafka-based event-driven pipelines
- Partner with product teams to take features from design through production

Requirements:
- 5+ years building backend services in Java or Python
- Strong grounding in system design, scalability and concurrency
- Experience with PostgreSQL and Redis
- Familiarity with AWS, Docker and CI/CD pipelines
"""


def print_diagnostics() -> bool:
    """Print environment state. Returns True if generation can proceed."""
    print("--- Resume module diagnostics ---")
    print(f"Config          : {config.describe()}")
    print(f"Prompt file     : {config.PROMPT_FILE}")
    print(f"Base resume     : {config.BASE_RESUME_FILE}")
    print(f"Output dir      : {config.OUTPUT_DIR}")

    ok = True

    for label, path in (("Prompt file", config.PROMPT_FILE),
                        ("Base resume", config.BASE_RESUME_FILE)):
        if not path.exists():
            print(f"  MISSING       : {label} not found at {path}")
            ok = False

    if config.BASE_RESUME_FILE.exists():
        regions = split_dynamic(load_base_resume() or "")
        print(f"Tailorable      : {', '.join(regions) if regions else 'none'}")
        if not regions:
            print("  WARNING       : no RESUME_DYNAMIC_START(...) regions — the base "
                  "resume will be used unmodified")

    engine = find_engine()
    if engine:
        print(f"LaTeX engine    : {engine}")
        base = load_base_resume()
        if base:
            warning = check_engine_compatibility(base, engine)
            if warning:
                print(f"  INCOMPATIBLE  : {warning}")
                ok = False
    else:
        print("LaTeX engine    : NONE FOUND — install one to produce PDFs")
        print("                  macOS: brew install tectonic")
        print("                  Debian/Ubuntu: apt install texlive-latex-recommended")
        ok = False

    if config.RESUME_GEMINI_ENABLED and not config.GEMINI_API_KEY:
        print("Gemini          : enabled but GEMINI_API_KEY is empty "
              "— will fall back to the base resume")
    elif not config.RESUME_GEMINI_ENABLED:
        print("Gemini          : off — base resume will be used unmodified")
    else:
        try:
            import google.genai  # noqa: F401
            print("Gemini          : ready")
        except ImportError:
            print("Gemini          : google-genai NOT INSTALLED "
                  "— run: pip install google-genai")
            ok = False

    print("---")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the resume generation module in isolation.",
    )
    parser.add_argument("--title", default="Senior Backend Engineer")
    parser.add_argument("--company", default="Example Corp")
    parser.add_argument("--location", default="Bangalore, India")
    parser.add_argument("--jd", type=Path,
                        help="File containing the job description (plain text or HTML)")
    parser.add_argument("--jd-text", help="Job description passed inline")
    parser.add_argument("--check", action="store_true",
                        help="Print diagnostics and exit without generating")
    parser.add_argument("--dump-latex", type=Path, metavar="PATH",
                        help="Write the tailored LaTeX here instead of compiling a PDF")
    parser.add_argument("--show-payload", action="store_true",
                        help="Print the exact text that would be sent to Gemini, then "
                             "exit without calling it")
    parser.add_argument("--send", action="store_true",
                        help="Also send the finished PDF to Telegram")
    args = parser.parse_args()

    ready = print_diagnostics()
    if args.check:
        return 0 if ready else 1

    if args.jd:
        try:
            description = args.jd.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"ERROR: could not read {args.jd}: {exc}")
            return 1
    elif args.jd_text:
        description = args.jd_text
    else:
        print("No --jd or --jd-text given; using the built-in sample posting.\n")
        description = SAMPLE_JD

    # --show-payload never calls Gemini; it just prints what would be sent.
    if args.show_payload:
        payload = build_payload(args.title, args.company, args.location, description)
        if not payload:
            return 1
        print("=" * 70)
        print(payload)
        print("=" * 70)
        print(f"\n{len(payload)} characters would be sent to {config.GEMINI_MODEL}.")
        regions = split_dynamic(load_base_resume() or "")
        print(f"{len(regions)} tailorable region(s): {', '.join(regions) or 'none'}.")
        print("The preamble and the contact header above the first \\section are "
              "not part of the payload — search the text above for any personal "
              "detail that still appears.")
        return 0

    # --dump-latex stops before compiling, so it works without a LaTeX install.
    if args.dump_latex:
        base = load_base_resume()
        if not base:
            return 1
        latex = tailor_latex(args.title, args.company, args.location, description, base)
        args.dump_latex.parent.mkdir(parents=True, exist_ok=True)
        args.dump_latex.write_text(latex, encoding="utf-8")
        print(f"\nLaTeX written to {args.dump_latex} ({len(latex)} chars)")
        return 0

    if not ready:
        print("Diagnostics reported problems — attempting generation anyway.\n")

    pdf_path = generate_resume(
        title=args.title,
        company=args.company,
        location=args.location,
        description=description,
    )

    if not pdf_path:
        print("\nFAILED — no PDF produced. Check logs/resume.*.log for the reason.")
        return 1

    print(f"\nPDF written to {pdf_path} ({pdf_path.stat().st_size} bytes)")

    if args.send:
        from common.notifications.telegram import is_configured, send_document
        if not is_configured():
            print("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
            return 1
        message_id = send_document(
            str(pdf_path),
            caption=f"📄 Test resume for <b>{args.title}</b> at <b>{args.company}</b>",
        )
        print("Sent to Telegram." if message_id else "Telegram send FAILED — see logs.")
        return 0 if message_id else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

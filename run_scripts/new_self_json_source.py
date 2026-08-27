#!/usr/bin/env python3
"""Authoring and dry-run tool for the self_json borg.

Every other part of this borg refuses to guess: if the sheet does not name a
path, the company is skipped. That rule makes runtime behaviour predictable but
leaves someone having to work out the paths in the first place, which is what
this tool is for. It is the *only* place auto-detection happens, and a human
reads its output before anything reaches the sheet.

    Scaffold a spec from a curl:
        PYTHONPATH=. python3 run_scripts/new_self_json_source.py --curl-file acme.txt

    Dry-run a spec (no DB writes, no Telegram):
        PYTHONPATH=. python3 run_scripts/new_self_json_source.py \
            --curl-file acme.txt --spec-file acme.json --dry-run
        PYTHONPATH=. python3 run_scripts/new_self_json_source.py --company "Acme" --dry-run
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import requests

from borgs.self_json.curl import CurlError, parse_curl
from borgs.self_json.scraper import SelfJsonScraper
from borgs.self_json.spec import (
    SpecError,
    parse_posted,
    parse_spec,
    resolve_field,
    resolve_path,
    resolve_scalar,
)
from common.analyzer import analyze_description
from common.constants import DESC_SCORE_THRESHOLD
from common.filters import title_matches

# Field name -> substrings that usually indicate it, used only to pre-fill the
# prompts below. Nothing here ever reaches the sheet unreviewed.
_HINTS = {
    "job_id": ("positionid", "requisitionid", "jobid", "reqid", "id"),
    "title": ("postingtitle", "jobtitle", "title", "name"),
    "location": ("location", "city", "country", "region", "office"),
    "posted": ("postdate", "posteddate", "publisheddate", "publishedat",
               "createddate", "postedon", "datposted"),
    "description": ("jobsummary", "descriptionplain", "description", "summary",
                    "jobdescription", "content"),
}

MAX_CANDIDATES = 8
MAX_LEAVES = 120


# ---------------------------------------------------------------------------
# Response introspection
# ---------------------------------------------------------------------------

def find_job_arrays(payload, path="", found=None, depth=0):
    """Every list-of-objects in the response, deepest-scoring first.

    KLM buries its jobs in an Elasticsearch envelope, so the useful answer is
    'hits.hits[]._source' rather than 'hits.hits' — when every element of a list
    has a single dict-valued key, the wrapper is reported too.
    """
    if found is None:
        found = []
    if depth > 6:
        return found

    if isinstance(payload, list):
        objects = [item for item in payload if isinstance(item, dict)]
        if objects:
            found.append((path, len(payload), sorted(objects[0].keys())))
            # Unwrap a uniform single-key envelope (_source, node, fields, ...).
            common_keys = set(objects[0].keys())
            for item in objects[1:]:
                common_keys &= set(item.keys())
            for key in sorted(common_keys):
                if all(isinstance(item.get(key), dict) for item in objects):
                    inner = objects[0][key]
                    found.append((
                        f"{path}[].{key}", len(objects), sorted(inner.keys())
                    ))
        return found

    if isinstance(payload, dict):
        for key, value in payload.items():
            child = f"{path}.{key}" if path else key
            find_job_arrays(value, child, found, depth + 1)

    return found


def leaf_paths(obj, prefix="", out=None, depth=0):
    """Every scalar-bearing path in a sample job, for filling in mappings.

    Goes deeper than looks necessary because rich-text descriptions nest: KLM's
    Portable Text needs 'description[].children[].text', which is four levels
    down and is the single most important path on the whole board.
    """
    if out is None:
        out = []
    if depth > 6 or len(out) >= MAX_LEAVES:
        return out

    if isinstance(obj, dict):
        for key, value in obj.items():
            leaf_paths(value, f"{prefix}.{key}" if prefix else key, out, depth + 1)
    elif isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            leaf_paths(obj[0], f"{prefix}[]", out, depth + 1)
        elif obj:
            out.append((prefix, obj[0]))
    else:
        out.append((prefix, obj))
    return out


def _suggest(field, leaves):
    """Best-guess path for a field, or None. Only ever a prompt default.

    Rich text nests, so the paths matching a name hint include the block's own
    attributes: KLM offers description[].style ("normal") alongside
    description[].children[].text (the actual prose). Name matching alone would
    happily suggest the first, producing a spec that validates and then extracts
    the word "normal" a hundred times, so the longest sample value wins.
    """
    matches = []
    for rank, hint in enumerate(_HINTS.get(field, ())):
        for path, value in leaves:
            # A flag is never an id, title, location or date. Apple's
            # isMultiLocation matches the 'location' hint on name alone and
            # would otherwise beat locations[].name by a single character.
            if isinstance(value, bool):
                continue
            flat = path.lower().replace("_", "")
            empty = 0 if str(value).strip() else 1
            if flat.endswith(hint):
                matches.append((rank, 0, empty, len(str(value)), path))
            elif hint in flat:
                matches.append((rank, 1, empty, len(str(value)), path))
    if not matches:
        return None
    if field == "description":
        # Prose beats a same-named attribute, whichever hint matched it.
        return max(matches, key=lambda m: (m[3], -m[0], -m[1]))[4]
    # Everywhere else: a path that actually carries a value, then the most
    # direct one — 'id' over 'jobArea.id'.
    return min(matches, key=lambda m: (m[0], m[1], m[2], len(m[4]), m[4]))[4]


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------

def _ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or (default or "")


def scaffold(curl_text):
    request = parse_curl(curl_text)
    print(f"\nRequest: {request.method} {request.url}")
    print(f"Headers: {len(request.headers)} (replayed verbatim)")
    if request.json_body:
        print(f"JSON body keys: {list(request.json_body)}")
    print("\nExecuting...")

    response = requests.request(
        request.method, request.url, headers=request.headers,
        json=request.json_body, data=request.data, timeout=30,
    )
    print(f"HTTP {response.status_code}, {len(response.content)} bytes")
    if response.status_code != 200:
        print("Non-200 — nothing to introspect.")
        return 1
    try:
        payload = response.json()
    except ValueError:
        print("Response is not JSON — this borg only handles JSON APIs.")
        return 1

    candidates = find_job_arrays(payload)
    if not candidates:
        print("No list-of-objects found anywhere in the response.")
        return 1

    candidates.sort(key=lambda c: (c[1], len(c[2])), reverse=True)
    print("\nCandidate job arrays:")
    for index, (path, count, keys) in enumerate(candidates[:MAX_CANDIDATES], 1):
        shown = ", ".join(keys[:10]) + (", ..." if len(keys) > 10 else "")
        print(f"  [{index}] {path or '(root)'}  — {count} items")
        print(f"      keys: {shown}")

    choice = _ask("\nWhich array holds the jobs? (number or a path)", "1")
    if choice.isdigit() and 1 <= int(choice) <= len(candidates[:MAX_CANDIDATES]):
        jobs_path = candidates[int(choice) - 1][0]
    else:
        jobs_path = choice

    sample_list = resolve_path(payload, jobs_path)
    if not isinstance(sample_list, list) or not sample_list:
        print(f"'{jobs_path}' did not resolve to a non-empty list.")
        return 1
    sample = sample_list[0]

    leaves = leaf_paths(sample)
    print(f"\nPaths available on a job ({len(leaves)} shown), relative to '{jobs_path}':")
    for path, value in leaves:
        preview = str(value).replace("\n", " ")[:60]
        print(f"  {path:45} {preview}")

    print("\nFill in each mapping. Comma-separate paths to concatenate them.")
    fields = {}
    for name in ("job_id", "title", "location", "posted", "description"):
        answer = _ask(f"  {name}", _suggest(name, leaves))
        if not answer:
            if name == "description":
                print("    (no description path — you must add a 'detail' block by hand)")
            continue
        parts = [part.strip() for part in answer.split(",") if part.strip()]
        fields[name] = parts if len(parts) > 1 else parts[0]

    posted_format = _ask(
        "  posted_format (iso8601/epoch_seconds/epoch_millis/relative_text/%-format)",
        "iso8601",
    )

    link_path = _ask("  application_link path (blank to use a URL template)")
    link_template = ""
    if not link_path:
        print("    Template placeholders may use any field name, e.g. {job_id}.")
        print("    Add extra fields for URL slugs as name=path, comma-separated.")
        extra = _ask("    extra fields (blank for none)")
        for pair in [p for p in extra.split(",") if "=" in p]:
            key, _, value = pair.partition("=")
            fields[key.strip()] = value.strip()
        link_template = _ask("    link_template")
    else:
        fields["application_link"] = link_path

    location_filter = _ask(
        "  location_filter (india / any / comma-separated keywords)", "india"
    )
    if location_filter not in ("india", "any"):
        location_filter = [k.strip().lower() for k in location_filter.split(",") if k.strip()]

    spec = {"jobs_path": jobs_path, "fields": fields, "posted_format": posted_format}
    if link_template:
        spec["link_template"] = link_template
    spec["location_filter"] = location_filter

    page_field = _ask("  pagination field in the request body (blank for none)", "page")
    if page_field:
        spec["pagination"] = {
            "type": "page", "in": "json" if request.json_body else "query",
            "page_field": page_field, "start": 1, "max_pages": 10,
            "delay_seconds": 0.4,
        }

    rendered = json.dumps(spec, indent=2)
    print("\n" + "=" * 70)
    try:
        parsed = parse_spec(rendered)
    except SpecError as exc:
        print(f"SPEC IS NOT VALID YET: {exc}\n")
        print(rendered)
        return 1

    # No API this borg was built against returns an application URL, so the
    # template is always hand-written — and a broken Apply button is invisible
    # until someone taps it.
    if parsed.link_template:
        values = {k: resolve_field(sample, v, " ") for k, v in parsed.fields.items()}
        try:
            link = parsed.link_template.format(**values)
            # Reuse the curl's identity headers — KLM stalls a bare request from
            # a non-browser exactly as it does on the API itself. The CORS and
            # content-type headers describe an API call, not a page visit.
            probe = {
                k: v for k, v in request.headers.items()
                if k.lower() not in ("accept", "content-type", "sec-fetch-dest",
                                     "sec-fetch-mode", "sec-fetch-site")
            }
            probe["accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            response = requests.get(link, headers=probe, timeout=20,
                                    allow_redirects=True, stream=True)
            response.close()
            marker = "ok" if response.status_code < 400 else "LOOKS WRONG"
            print(f"Apply link check: {link}\n  -> HTTP {response.status_code} ({marker})")
        except Exception as exc:
            print(f"Apply link check inconclusive ({type(exc).__name__}) — open it by hand:")
            print(f"  {link}")

    print("\nValid. Paste the curl into 'ATS Link' and this into 'Job Spec':\n")
    print(rendered)
    return 0


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def _detach_learned_weights():
    """Score with the base keyword weights when the DB is out of reach.

    analyze_description() reads learned multipliers from keyword_weight_overrides,
    which is right for the borg but wrong for this tool: a spec often needs
    verifying from a laptop that cannot see MySQL, and before the company exists
    as a row at all. The borg itself still fails loudly on a DB outage.
    """
    import common.analyzer as analyzer
    from common.db.repository import load_keyword_weight_overrides

    try:
        load_keyword_weight_overrides()
        return False
    except Exception:
        analyzer.load_keyword_weight_overrides = lambda: {}
        return True


def dry_run(curl_text, spec_text, company_name):
    """Run the whole pipeline and print the funnel. Writes nothing anywhere."""
    scraper = SelfJsonScraper(curl_text, spec_text, company_name=company_name)
    spec = scraper.spec
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    jobs = scraper._fetch_all(cutoff)
    print(f"\n{company_name} — funnel")
    print(f"  returned by API      : {len(jobs)}")
    if not jobs:
        print("\n  Zero jobs before any filter — the curl is probably stale.")
        return 1

    dated, undated = [], 0
    for job in jobs:
        posted = parse_posted(resolve_scalar(job, spec.fields["posted"]), spec.posted_format)
        if posted is None:
            undated += 1
        else:
            dated.append((job, posted))
    print(f"  posting date readable: {len(dated)}  ({undated} unreadable)")

    recent = [(j, d) for j, d in dated if d >= cutoff]
    print(f"  posted in last 24h   : {len(recent)}")

    titled = [(j, d) for j, d in dated if title_matches(resolve_field(j, spec.fields["title"], " "))]
    print(f"  title matches (all)  : {len(titled)}")

    located = [
        (j, d) for j, d in titled
        if scraper._location_ok(resolve_field(j, spec.fields["location"], ", "))
    ]
    print(f"  location matches     : {len(located)}")

    # Scored against the whole board, not just the last 24h, so a spec can be
    # verified on a quiet day. The borg itself always applies the 24h cutoff.
    if _detach_learned_weights():
        print("\n  (database unreachable — scoring with base keyword weights only)")
    print(f"\n  Ignoring the 24h cutoff, {len(located)} job(s) would qualify:")
    passing = 0
    for job, posted in located[:15]:
        values = scraper._values_for(job)
        if spec.detail:
            scraper._apply_detail(values)
        analysis = analyze_description(values.get("description", ""))
        link = (spec.link_template.format(**values) if spec.link_template
                else values.get("application_link", ""))
        verdict = "KEEP" if analysis["score"] >= DESC_SCORE_THRESHOLD else "drop"
        passing += verdict == "KEEP"
        print(f"\n  [{verdict}] score={analysis['score']:>3}  {values.get('title', '')[:60]}")
        print(f"        id={resolve_scalar(job, spec.fields['job_id'])}  "
              f"posted={posted:%Y-%m-%d}  loc={values.get('location', '')[:40]}")
        print(f"        desc={len(values.get('description', ''))} chars  "
              f"keywords={', '.join(analysis['positive_matches'][:6])}")
        print(f"        {link}")

    print(f"\n  {passing} of {min(len(located), 15)} shown clear the score threshold "
          f"({DESC_SCORE_THRESHOLD}).")
    print("  Nothing was written to the database and no Telegram message was sent.")
    return 0


def _load_from_db(company_name):
    from common.db.repository import load_self_json_companies

    for company in load_self_json_companies():
        if company["name"].lower() == company_name.lower():
            return company
    raise SystemExit(
        f"No enabled company named {company_name!r} with ATS 'self_json'. "
        "Check the sheet and run run_scripts/sync_companies.py."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curl-file", help="file holding the curl (or - for stdin)")
    parser.add_argument("--spec-file", help="file holding the Job Spec JSON")
    parser.add_argument("--company", help="read curl and spec from company_info")
    parser.add_argument("--dry-run", action="store_true",
                        help="run the pipeline and print the funnel; writes nothing")
    args = parser.parse_args()

    curl_text = spec_text = None
    company_name = args.company or "dry-run"

    if args.company:
        company = _load_from_db(args.company)
        curl_text, spec_text = company["curl"], company["spec"]
        company_name = company["name"]
    else:
        if not args.curl_file:
            parser.error("one of --curl-file or --company is required")
        curl_text = (sys.stdin.read() if args.curl_file == "-"
                     else open(args.curl_file).read())
        if args.spec_file:
            spec_text = open(args.spec_file).read()

    try:
        if args.dry_run or spec_text:
            if not spec_text:
                parser.error("--dry-run needs a spec: pass --spec-file or --company")
            return dry_run(curl_text, spec_text, company_name)
        return scaffold(curl_text)
    except (CurlError, SpecError) as exc:
        print(f"\nConfiguration error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

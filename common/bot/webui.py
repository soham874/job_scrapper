"""Shared chrome for the read-only web pages — tokens, tab strip, page shell.

There are two pages now: the application tracker and the analytics view. They
show different things and share nothing but the look, the URL token and the
strip of tabs that moves between them. That is exactly the part worth having
in one place — a second copy of the colour tokens is a second copy that drifts,
and the first thing anyone would notice is one page going dark-mode-correct
while the other did not.

What stays with each page is its own layout CSS and its own script. This module
only owns the frame: the palette, the type, the header, the tabs, and the
handful of primitives both pages build out of (cards, the filter row, pills).

Authorisation lives here too, for the same reason. Both pages are read-only
views of the same data, so a token that opened one and not the other would be
a distinction without a difference — and the kind that gets forgotten when a
third page arrives.
"""

import html
import os
from typing import Optional
from urllib.parse import urlencode

# Optional shared secret. Unset serves the pages to anyone who finds the URL —
# fine behind a private network or tunnel, not on the public host the webhook
# needs. Set it and every page requires ?t=<token>.
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")

# Path, label. Order is tab order.
TABS = (
    ("/dashboard", "Applications"),
    ("/analytics", "Analytics"),
)


def authorized(token: Optional[str]) -> bool:
    return not DASHBOARD_TOKEN or token == DASHBOARD_TOKEN


def e(value) -> str:
    """HTML-escape, with None and empty string collapsing to ''."""
    return html.escape(str(value)) if value not in (None, "") else ""


def tabs(active: str, token: Optional[str]) -> str:
    """The tab strip, with the token threaded through every link.

    Without that the first tap off a token-protected page lands on a 404, which
    looks like the feature is broken rather than like the link is incomplete.
    """
    query = f"?{urlencode({'t': token})}" if DASHBOARD_TOKEN and token else ""
    out = []
    for path, label in TABS:
        on = path == active
        classes = "tab is-on" if on else "tab"
        current = ' aria-current="page"' if on else ""
        out.append(
            f'<a class="{classes}" href="{e(path + query)}"{current}>{e(label)}</a>'
        )
    return "".join(out)


# Placeholders are HTML comments rather than str.format fields: the CSS and the
# scripts these pages carry are full of braces, and doubling every one of them
# to survive .format() is how a stylesheet quietly acquires a syntax error.
def render(template: str, **parts) -> str:
    for name, value in parts.items():
        template = template.replace(f"<!--{name.upper()}-->", value)
    return template


# The palette both pages draw on. The greens are the product's own; the eight
# --series-* slots are a categorical set validated for colour-vision deficiency
# in both modes (worst adjacent pair ΔE 9.1 light / 8.4 dark, OKLab ×100), and
# the order is the safety mechanism rather than decoration — reshuffling it
# breaks pairs that currently clear the gate. The dark column is the same eight
# hues re-stepped for a dark surface, not an automatic lightening of the light
# ones. Three light slots sit under 3:1 against white, which is why anything
# they colour is also named in a legend and repeated in a table.
_TOKENS = """
  :root {
    --bg:#fbfbfa; --fg:#1a1a18; --muted:#6b6b66; --faint:#8d8d86;
    --line:#e4e4e0; --card:#ffffff; --raised:#f4f4f1;
    --accent:#2f6f4f; --accent-soft:#e6f1ea;
    --ok:#2f6f4f; --ok-bg:#e8f2ec; --warn:#8a6116; --warn-bg:#faefd9;
    --hot:#a33a2a; --hot-bg:#fbe9e5;
    --grid:#ecece8; --seq-track:#dfebfa; --seq-fill:#2a78d6;
    --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a; --series-4:#eda100;
    --series-5:#e87ba4; --series-6:#008300; --series-7:#4a3aa7; --series-8:#e34948;
    --shadow:0 1px 2px rgba(0,0,0,.05); --radius:12px; --gutter:16px;
  }
  @supports (padding: max(0px)) {
    :root { --gutter: max(16px, env(safe-area-inset-left)); }
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#141417; --fg:#e9e9e5; --muted:#9a9a94; --faint:#7c7c76;
      --line:#2b2b31; --card:#1d1d22; --raised:#26262c;
      --accent:#7fc4a0; --accent-soft:#1e3a2c;
      --ok:#7fc4a0; --ok-bg:#1c3328; --warn:#e0b767; --warn-bg:#332912;
      --hot:#f0907c; --hot-bg:#3a1f1a;
      --grid:#2f2f36; --seq-track:#23303f; --seq-fill:#3987e5;
      --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; --series-4:#c98500;
      --series-5:#d55181; --series-6:#008300; --series-7:#9085e9; --series-8:#e66767;
      --shadow:none;
    }
  }
"""

_BASE = """
  * { box-sizing:border-box; }
  html { -webkit-text-size-adjust:100%; }
  body {
    margin:0; background:var(--bg); color:var(--fg);
    font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Inter,sans-serif;
    padding:18px var(--gutter) calc(48px + env(safe-area-inset-bottom));
    overflow-x:hidden;
  }
  .shell { max-width:1280px; margin:0 auto; }
  h1 { font-size:19px; margin:0; letter-spacing:-.01em; }
  .sub { color:var(--muted); font-size:12.5px; margin:3px 0 0; }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }
  a:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }
  .muted { color:var(--muted); }
  [hidden] { display:none !important; }

  /* Title left, tabs right, stacking under 560px. The tabs deliberately do not
     stick: each page already pins its own filter row, and two stacked sticky
     strips eat the top third of a phone screen. */
  .masthead { display:flex; align-items:flex-start; gap:12px 20px; flex-wrap:wrap; }
  .masthead .titles { flex:1 1 220px; min-width:0; }
  .tabs {
    display:flex; gap:4px; padding:3px; flex:0 0 auto;
    background:var(--raised); border:1px solid var(--line); border-radius:999px;
  }
  .tab {
    display:flex; align-items:center; padding:7px 15px; min-height:36px;
    border-radius:999px; font-size:13px; font-weight:500; color:var(--muted);
    white-space:nowrap; text-decoration:none;
  }
  .tab:hover { color:var(--fg); text-decoration:none; }
  .tab.is-on { background:var(--card); color:var(--fg); box-shadow:var(--shadow); }
  .tab:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }

  /* The filter row. One per page, above everything it scopes, so every number
     on the page is read off the same slice. */
  .toolbar {
    position:sticky; top:0; z-index:5; background:var(--bg);
    display:flex; align-items:center; gap:12px;
    margin:12px calc(-1 * var(--gutter)) 0;
    padding:8px var(--gutter); border-bottom:1px solid var(--line);
  }
  .chips {
    flex:1 1 auto; display:flex; gap:8px; min-width:0;
    overflow-x:auto; scrollbar-width:none; -webkit-overflow-scrolling:touch;
    padding:2px 0;
  }
  .chips::-webkit-scrollbar { display:none; }
  .chip {
    flex:0 0 auto; display:flex; flex-direction:column; gap:1px; align-items:flex-start;
    background:var(--card); border:1px solid var(--line); border-radius:10px;
    padding:6px 11px; min-width:72px; min-height:44px; cursor:pointer;
    font:inherit; color:inherit; text-align:left; box-shadow:var(--shadow);
    transition:border-color .12s, background .12s;
  }
  .chip:hover { border-color:var(--accent); }
  .chip .n { font-size:16px; font-weight:600; line-height:1.2; font-variant-numeric:tabular-nums; }
  .chip .k { font-size:10.5px; color:var(--muted); text-transform:lowercase; white-space:nowrap; }
  .chip.is-on { background:var(--accent-soft); border-color:var(--accent); }
  .chip.is-on .k { color:var(--accent); }
  .chip:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }

  .empty { padding:44px 20px; text-align:center; color:var(--muted); }
  footer { color:var(--muted); font-size:12px; margin:14px 0 0; max-width:70ch; }

  @media (max-width:560px) {
    .masthead { gap:10px; }
    .tabs { width:100%; }
    .tab { flex:1 1 0; justify-content:center; }
  }
  @media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
"""

SHELL_CSS = _TOKENS + _BASE

_DOC = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex, nofollow">
<title><!--TITLE--></title>
<style><!--CSS--></style></head>
<body>
<div class="shell">
<header class="masthead">
  <div class="titles">
    <h1><!--HEADING--></h1>
    <p class="sub"><!--SUB--></p>
  </div>
  <nav class="tabs" aria-label="Views"><!--TABS--></nav>
</header>
<!--BODY-->
</div>
<script>
<!--SCRIPT-->
</script>
</body></html>"""


def document(*, title: str, heading: str, sub: str, active: str,
             token: Optional[str], css: str, body: str, script: str = "") -> str:
    """Wrap a page's own markup in the shared frame.

    `sub` is escaped here because every caller so far builds it out of counts
    and dates; `body`, `css` and `script` are markup the page composed itself
    and are inserted as-is.
    """
    return render(
        _DOC,
        title=e(title),
        heading=e(heading),
        sub=e(sub),
        tabs=tabs(active, token),
        css=SHELL_CSS + css,
        body=body,
        script=script,
    )

# Resume tailoring prompt

Edit this file to change tailoring behaviour — no code changes needed.

Placeholders substituted at runtime: `<<JOB_TITLE>>`, `<<COMPANY>>`,
`<<LOCATION>>`, `<<JOB_DESCRIPTION>>`, `<<RESUME_BODY>>`.

Which parts of the resume are tailorable is decided in `base_resume.tex`, not
here: wrap a region in `% RESUME_DYNAMIC_START(name)` / `% RESUME_DYNAMIC_END`
and it becomes editable; unwrap it and it goes back to being static.

---

You are tailoring an existing LaTeX resume for one specific job posting.

## What you may and may not do

You are **selecting, reordering, and rewording content that already exists** in
the base resume. You are not writing a new resume.

Permitted:

- Reorder bullets within a role so the most relevant appear first — but see the
  rule on sub-role headers below, which constrains how far a bullet may move.
- Reword a bullet to use the posting's vocabulary for a thing the bullet already
  describes — for example, if the bullet says "microservices" and the posting
  says "service-oriented architecture", either wording is fine because it is the
  same work.
- Drop a bullet entirely when it is irrelevant to this posting and the resume
  needs the space.
- Rewrite the summary to emphasise the parts of the candidate's real background
  this posting cares about.

You may draw on the static sections shown for context — the skills list, the
education and awards — when judging what to emphasise. You cannot edit them.

Forbidden — these are the failure modes that matter most:

- **Never introduce a fact that is not in the base resume.** No new employers,
  job titles, dates, degrees, certifications, technologies, tools, or metrics.
  If the posting asks for Kubernetes and the resume does not mention Kubernetes,
  the tailored resume still does not mention Kubernetes.
- **Never inflate scope or seniority.** If the base says "contributed to" or
  "helped build", the tailored version must not say "led", "owned", "drove", or
  "architected". Keep the same claim about the candidate's role at the same
  strength. This is the single most important rule — an inflated verb reads as a
  small edit and becomes a serious problem in an interview.
- **Never invent or adjust numbers.** Percentages, latencies, team sizes, user
  counts, and revenue figures carry over exactly as written or not at all.
- **Never move a bullet across a sub-role header.** Some bullets are not
  achievements at all — they are title markers for a promotion within the same
  employer, and look like `\resumeItem{\textbf{Some Title} \hfill Month Year --
  Month Year}`. Treat every such bullet as a fixed divider: keep it exactly
  where it is, in its original order, and reorder or drop ordinary bullets only
  *within* the group that follows it. Moving an achievement above its title
  marker reassigns that work to a more senior role the candidate held later,
  which is a false seniority claim even though every word is unchanged. Leave
  any `\vspace{...}` separating those groups in place too.
- Never add a skills section, a certifications section, or any other section
  that the base resume does not already have.

If the posting wants something the candidate genuinely does not have, leave that
gap visible. A resume with an honest gap is the correct output.

## Format requirements

- The resume below is marked up with editable regions that look like this:

      % RESUME_DYNAMIC_START(summary)
      ...LaTeX...
      % RESUME_DYNAMIC_END

  **Return only those regions**, each wrapped in its own original
  `% RESUME_DYNAMIC_START(name)` / `% RESUME_DYNAMIC_END` pair, with the name
  spelled exactly as it appears below. Everything outside the markers is shown
  for context only: it is already in the final document and must not be repeated
  in your answer.
- Return every region you were given. If a region needs no change for this
  posting, return it unchanged rather than omitting it.
- **Never return `\documentclass`, `\usepackage`, `\begin{document}` or
  `\end{document}`.** You are producing fragments that get spliced into an
  existing document, not a document.
- Braces must balance within each region, counting `\{` and `\}` as literal
  characters rather than grouping.
- **Preserve every custom macro exactly, including its argument count.** This
  resume defines its own commands (for example `\resumeItem{...}` and
  `\resumeSubheading{...}{...}{...}{...}`, along with paired
  `\resumeSubHeadingListStart` / `\resumeSubHeadingListEnd` and
  `\resumeItemListStart` / `\resumeItemListEnd` wrappers). Rewrite the text
  *inside* the braces. Never change how many arguments a macro takes, never
  rename one, never replace one with plain LaTeX equivalents, and never leave a
  list environment unclosed — any of those stop the document compiling.
- Escape LaTeX special characters in any text you write: `&` `%` `$` `#` `_`
  `{` `}` become `\&` `\%` `\$` `\#` `\_` `\{` `\}`.
- Keep the resume to the same page count as the base — if trimming is needed to
  hold one page, drop the least relevant bullets rather than shrinking margins
  or font size.
- Output **only** the marked regions. No markdown code fences, no preamble like
  "Here is the tailored resume", no commentary, no explanation of your changes.
  The first characters of your response must be `% RESUME_DYNAMIC_START`.

## The job posting

Title: <<JOB_TITLE>>
Company: <<COMPANY>>
Location: <<LOCATION>>

Description:

<<JOB_DESCRIPTION>>

## The resume body (LaTeX source)

Regions wrapped in `% RESUME_DYNAMIC_START(name)` / `% RESUME_DYNAMIC_END` are
yours to rewrite and are the only thing to return. Everything else is context.

<<RESUME_BODY>>

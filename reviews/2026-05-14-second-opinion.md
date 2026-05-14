# Second Opinion & GitHub-Ready Roadmap — 2026-05-14

**Brief:** review the code function-by-function, question every design decision
(including the D-entries), research current best practice, and produce a
phased plan to get this repo public-ready at its best.

**Posture:** report-only. No code changes. Every recommendation is cited or
grounded in a verified file:line.

**Companion:** yesterday's [tech-debt audit](2026-05-14-tech-debt-audit.md) is
the inventory of known debt. This document is the *design-and-coherence*
layer on top: which choices hold up under pressure, which are behind 2026
consensus, and how to ship a great-first-impression repo.

---

## TL;DR

After reading the code with fresh eyes, running five research deliverables
against the 2025–2026 consensus, and pressure-testing every load-bearing
decision in the project: **the architecture is sound. The presentation is
not yet.** Five of six backend choices match or are ahead of consensus
(citations in §1); two frontend choices are clearly behind (no TypeScript,
hand-written API client); the public-facing surface needs real work before
a stranger lands on the repo.

There is **one new code-level finding** beyond yesterday's audit:
`stats_service.compute_stats` walks every `jump.xml` on every call because
of a stale docstring claim that the SQLite index doesn't carry the stats
columns. The v4 schema (2026-04-28) added `aircraft`, `discipline`, and
`freefall_time_s` *specifically* to eliminate this walk — but the function
still does it. This is a real performance bug that lands as a P2 in the
GitHub-ready slice.

The single highest-leverage change is **DECISIONS.md → `docs/adr/NNNN-*.md`**
— at 342 KB it's now actively harming the project (slow render in GitHub,
unscannable for a new contributor, structurally behind 2025-2026 ADR
consensus). Doing the split before publishing is half a day of mechanical
work and meaningfully changes the repo's first impression.

A nine-phase roadmap is at the bottom. Each phase ships independently with
tests + lint green, per your CLAUDE.md §3 small-increments rule.

---

## Part 1 — Pressure-testing the load-bearing decisions

Each D-entry below got the same treatment: I read the rationale in
DECISIONS.md, read the implementing code, then asked "would I make this
choice today, given the 2026 evidence?" Verdicts:
**CONFIRMED** / **AHEAD** / **BEHIND** / **DRIFT**.

### 1.1 D2 — XML on disk + versioned XSD as source of truth → **CONFIRMED**

Pressure-tested against current Library of Congress Recommended Formats
Statement for Datasets ([loc.gov/preservation/resources/rfs/data.html](https://www.loc.gov/preservation/resources/rfs/data.html)),
JSON Schema Draft 2020-12 ([json-schema.org/draft/2020-12](https://json-schema.org/draft/2020-12)),
and the prior art across Obsidian, Logseq, Joplin, and Zotero.

Findings:
- LoC's current preservation guidance explicitly names **XML schemas** as
  the well-known, public-validator schema family for archival datasets.
  JSON Schema is *not* called out for the same role.
- JSON Schema 2020-12 lacks identity constraints (no equivalent of XSD's
  `xs:unique`, `xs:key`, `xs:keyref` — see
  [json-schema-vocabularies #22](https://github.com/json-schema-org/json-schema-vocabularies/issues/22)).
  For a logbook with `jump_number` uniqueness, person refs, and rig
  serials, that gap matters.
- **Pattern across prior art:** prose-heavy apps choose markdown
  (Obsidian, Joplin); heavily-relational apps choose SQLite primary
  (Zotero, [zotero.org/support/zotero_data](https://www.zotero.org/support/zotero_data));
  Logseq is *moving away* from markdown to SQLite/EDN because markdown
  can't capture all the structure
  ([logseq/docs db-version.md](https://github.com/logseq/docs/blob/master/db-version.md)).
  A skydiving jump is structured + typed but readable as text — XML+XSD
  is the exactly-right cell.

**Verdict:** ship as-is. Do not relitigate D2 before going public.

One soft addition: **consider Schematron for cross-field rules** the
XSD can't express ("freefall time ≤ altitude / typical fall rate"
type constraints). Schematron just refreshed as
[ISO/IEC 19757-3:2025](https://en.wikipedia.org/wiki/Schematron) and
composes with XSD. P3 priority — only if business rules start piling up
in the service layer.

### 1.2 D11 — pywebview as the desktop host → **CONFIRMED**

Pressure-tested against Tauri 2.x, Wails 2/3, Electron 41, Neutralino.

Findings:
- pywebview 6.x is actively maintained (v6.0 Aug 2025; v6.2.1 visible on
  Arch as of April 2026). Drag/drop full-path leak fixed in 6.0.
- The Tauri + Python sidecar pattern is functional but **not low-friction
  in 2026**: PyInstaller cold start
  ([tauri-apps discussion #9226](https://github.com/orgs/tauri-apps/discussions/9226)),
  child-process kill-on-quit needs a watchdog
  ([tauri#8139](https://github.com/tauri-apps/tauri/issues/8139)),
  AV false positives compound across NSIS + PyInstaller
  ([tauri#10649](https://github.com/tauri-apps/tauri/issues/10649)),
  no sidecar hot-reload in `tauri dev`
  ([tauri#4134](https://github.com/tauri-apps/tauri/issues/4134)).
- WebView2 on Win10 LTSC isn't worse than it was in 2024 — Microsoft
  extended Edge/WebView2 support on Win10 to October 2028
  ([windowslatest.com Aug 2025](https://www.windowslatest.com/2025/08/10/microsoft-wont-end-support-for-chromium-edge-and-pwas-on-windows-10-until-october-2028/)).
- Wails adds Go + still has the sidecar problem (strictly worse). Electron
  is 80-150 MB compressed (indefensible for a logbook). Neutralino is
  smaller but ecosystem-thin.
- Verso (Servo-in-Tauri) exists but Tauri team explicitly calls it
  "not as feature rich and powerful as the current backends ... yet."

**Verdict:** stay on pywebview. Reconsider only if you need (a) true
multi-window with rich native menus, (b) a mobile target, or (c) signing
becomes blocked by the macOS/PyInstaller Mach-O conflict
([pyinstaller#7937](https://github.com/pyinstaller/pyinstaller/issues/7937)).

### 1.3 D7 — services own logic, REST adapter is thin → **CONFIRMED**

FastAPI community is split between "services as classes with `Depends`"
and "services as plain functions called from thin routes." Both shapes are
defended in current 2024-2026 best-practices guides
([zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices),
[dev.to layered architecture](https://dev.to/markoulis/layered-architecture-dependency-injection-a-recipe-for-clean-and-testable-fastapi-code-3ioo)).

For *services that don't carry per-request infrastructure handles*
(which is exactly our case — `logbook_root` and `user_id` are pure inputs,
not stateful resources), the functional pattern is the safe lane. The
"over-engineered" critique runs the other way: putting `Depends` on
everything is what gets called over-engineered for single-user apps.

**Verdict:** ship as-is.

### 1.4 D10 — atomic_write everywhere → **CONFIRMED / EXEMPLARY**

Already verified in yesterday's audit (§6 of [2026-05-14-tech-debt-audit.md](2026-05-14-tech-debt-audit.md)).
F_FULLFSYNC on Darwin, parent-directory fsync after rename, citation-rich
comments. This is one of the project's strongest surfaces.

### 1.5 D16 — RFC 9457 problem+json for every error → **AHEAD OF CONSENSUS**

Most FastAPI codebases ship bare `HTTPException(detail=...)`. The
[official handling-errors tutorial](https://fastapi.tiangolo.com/tutorial/handling-errors/)
is silent on RFC 7807/9457. But for a project explicitly positioned as
"public-API-shaped," RFC 9457 is the right level of formality — and the
ecosystem caught up
([fastapi-problem-details](https://pypi.org/project/fastapi-problem-details/),
[NRWLDev/rfc9457](https://github.com/NRWLDev/rfc9457)).

**Verdict:** keep. This is a quality marker worth advertising in the README.

### 1.6 D27 — JSON logging + ContextVar correlation via pure-ASGI middleware → **AHEAD OF CONSENSUS**

Most FastAPI projects use Starlette's `BaseHTTPMiddleware`. This project
explicitly chose pure-ASGI because `BaseHTTPMiddleware` breaks ContextVar
propagation, with citations to the upstream Starlette issues in the code
([backend/observability/logging.py:193-202](../backend/observability/logging.py)).
That's textbook senior-engineer judgment. Keep.

### 1.7 D48 — loopback-only, no auth in v0.1 → **CONFIRMED**

Right call for a single-user desktop app. The bearerAuth scheme was
correctly dropped from the OpenAPI surface. When v0.2 ships LAN exposure
or multi-user, a new D-entry adds it back together with the middleware
that backs it — additive, not breaking.

### 1.8 D50 — re-entrant writer lock (RLock) → **CONFIRMED with stale docs**

Choice is right (cross-service write composition needs re-entrancy,
documented in the module). **But the docstring contradicts current code**
— this is the finding from yesterday's audit §1.1. `get_jump`,
`list_jump_files`, `get_rig`, `list_rigs` are all `@with_writer_lock`
because they can transitively write via `folder_reconcile`, but the
docstring says "Reads do not acquire the lock." Fix the docstring.

### 1.9 D51 — pyright strict for production, basic for tests → **CONFIRMED**

Matches 2024–2026 consensus. Meta's
[Typed Python in 2024 survey](https://engineering.fb.com/2024/12/09/developer-tools/typed-python-2024-survey-meta/)
found 67% mypy / 38% pyright adoption. Strict typing in new infrastructure
is now the default. Optional consideration:
[basedpyright](https://pydevtools.com/handbook/reference/basedpyright/)
is gaining traction as a strict-by-default fork — worth a look for v0.2
but no urgency.

### 1.10 D26 — SQLite migration via drop-and-reindex → **CONFIRMED with caveat**

The "drop tables, restamp `PRAGMA user_version`, let the caller reindex"
pattern is correct for a *rebuildable* index. The caveat is the
implementation detail flagged in yesterday's audit: the *idiomatic*
FastAPI shape for opening connections is `Depends(get_conn)` generator
([FastAPI SQL tutorial](https://fastapi.tiangolo.com/tutorial/sql-databases/),
[full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)),
not `result = open_index(root); try ... finally: result.conn.close()`.

The per-call profile (76 µs) justifies the current shape for a single-user
desktop app, but it reads as non-idiomatic to a FastAPI-fluent contributor
landing on the repo. The fix is mechanical — wrap `open_index` in a
`get_index_conn` dependency that yields the connection. Same memory cost,
shape readers expect. **Defer to v0.2** unless the GitHub-ready slice has
room.

### 1.11 No frontend TypeScript (no D-entry, project default) → **BEHIND CONSENSUS**

State of JS 2024 ([2024.stateofjs.com/en-US/usage](https://2024.stateofjs.com/en-US/usage/))
shows 67% of respondents writing more TS than JS, with the largest cohort
writing TS-only. On a *new* public repo in 2026, plain `.jsx` is a
minority signal that raises contributor cost.

The project's defense ("solo dev + smoke tests catch import drift") is
internally coherent, but `views.smoke.test.jsx` catches *imports*, not the
bugs TypeScript actually prevents (wrong payload shape, missing field on a
discriminated union, `undefined.foo` after a refactor). For a 15.5K-LOC
frontend with a 2,353-LOC modal, those are exactly the bugs you'd hit.

**Verdict:** migrate gradually. The lowest-cost path: enable
`allowJs + checkJs` with JSDoc type annotations at the file boundary, then
rename `.js` → `.ts` as you touch each file. Vite supports this natively.
Convert `api.js` and `LogJumpModal.jsx` first — biggest payoff. This is
also a precondition for adopting `openapi-typescript`, `react-hook-form`,
`shadcn/ui`, and TanStack Query (each of which assumes TS).

### 1.12 Hand-written `api.js` with 74 fetch wrappers → **BEHIND CONSENSUS**

The backend produces a proper OpenAPI 3.1 spec; the project hand-writes
74 fetch wrappers in one 885-LOC file. The 2025-2026 default is
**`openapi-typescript` + `openapi-fetch`** (~11K stars, smallest blast
radius). Run it on `pre-commit` against `/openapi.json`; every endpoint
becomes a typed call. See
[apisyouwonthate.com/newsletter/openapi-to-frontend](https://apisyouwonthate.com/newsletter/openapi-to-frontend/)
for the current taxonomy.

Orval (~7K stars) and Kubb (~3.5K, fastest-growing) are heavier — they
generate React Query hooks + Zod + MSW mocks. Reach for them after
adopting TanStack Query, not before.

### 1.13 Hand-rolled forms (LogJumpModal: 2,353 LOC, 45 hooks) → **BEHIND CONSENSUS**

`react-hook-form` (~7M weekly downloads, still the consensus default in
2025–2026 — see
[Makers' Den 2025 form-handling roundup](https://makersden.io/blog/composable-form-handling-in-2025-react-hook-form-tanstack-form-and-beyond))
collapses most of those 45 hooks into one `useForm` call with `zodResolver`
for validation. The break-even point cited across 2025 surveys is
**~3 fields + one of {async validation, conditional fields, multi-step,
dirty tracking}**. LogJumpModal blows past that by an order of magnitude.

### 1.14 Raw Tailwind without component library → **DRIFT**

[RedMonk April 2025](https://redmonk.com/kholterhoff/2025/04/22/ui-component-libraries-shadcn-ui-and-the-revenge-of-copypasta/)
and 105K+ stars say **shadcn/ui** is the de-facto default for new Tailwind
apps in 2025–2026. Critically: it's not an npm dependency — `npx shadcn
add dialog` writes Radix-based components into your tree. Adoption is
incremental and reversible. For a pywebview app you get Radix's keyboard
nav, focus traps, and ARIA for free — all of which would otherwise be
hand-rolled inside the 2,353-LOC modal.

### 1.15 No router → **MINOR DRIFT**

`App.jsx` does `const View = VIEWS[activeTab] || Profile;` via `useState`.
No URL state → no back button, no refresh-survives-state, no
bookmarkable filters. In pywebview this matters less than in a browser
(no shareable URLs), but the back button and bookmarkable list filters
are real UX wins.

The lowest-friction migration is **react-router v7 with
`createHashRouter`** (works under `file://` in the packaged app). Each
sidebar item becomes a route; modal open-state can live in search params
so the modal survives refresh. P3 priority.

### 1.16 No state management library → **CONFIRMED (with carve-out)**

At 15K LOC with no remote collaboration, "no state lib" is defensible.
2025 consensus
([Makers' Den 2025](https://makersden.io/blog/react-state-management-in-2025))
is **Zustand is the default *when you need one*, not because you've
crossed an LOC threshold**. Triggers are "5+ stacked context providers"
or "prop drilling 4+ levels deep" — neither of which a logbook app
naturally hits.

**Carve-out:** adopt TanStack Query for *server state*. Most CRUD-app
pain is cache invalidation, refetch-on-focus, optimistic updates —
Query handles all three. Once Query owns server state, residual client
state is small enough that `useState` is fine. Pairs naturally with
`openapi-fetch`. P3.

---

## Part 2 — New code-level findings (beyond yesterday's audit)

### 2.1 [P2] `stats_service.compute_stats` walks every `jump.xml` on every call — stale assumption

`backend/services/stats_service.py:8-14`:

> Why disk-walk and not SQL: the SQLite index today carries only the
> JumpSummary subset (id, jump_number, title, date, dropzone). Stats
> need `freefall_time_s` and `discipline` which aren't indexed.

But `backend/storage/index.py:41-46,106-108` (v3 → v4 schema bump,
2026-04-28):

> v4 (2026-04-28): denormalized cache of aircraft / discipline /
> freefall_time_s onto jumps so the JumpsLog UI surfaces them without
> per-row XML reads.

The columns are there. The function still walks. On a logbook with
500 jumps, every `GET /api/v1/stats` call parses 500 XML files +
runs 500 XSD validations + builds 500 Pydantic models, when a single
`SELECT date, freefall_time_s, discipline, dropzone FROM jumps WHERE
user_id = ? AND date >= ?` would do.

**Cost:** linear in jump count. Cheap at 100, slow at 5,000. Yesterday's
profile of 76 µs per `open_index` is wholly dwarfed by per-jump XML
parse + XSD validate cost (~ms each).

**Fix:** rewrite `compute_stats` to a SQL aggregate over the index, with
a fallback to disk-walk for the columns SQL doesn't cover (none today;
`year_by_month` is derivable from `date`). Update the docstring. Add a
test that asserts no XML parse happens for stats with a valid index.

This is the single new P2 from this review pass.

### 2.2 [P1] `App.jsx` initial-tab `useState` defeats "open where you left off"

`frontend/src/App.jsx:21` hardcodes `useState('profile')`. Refreshing
the desktop app always lands on Profile, regardless of where the user
was. Two paths:
- **Quick fix (no router):** persist `activeTab` to `localStorage` on
  change; read it on mount. ~5 LOC. Solves "open where I was."
- **Right fix (with router, P3):** URL drives tab; back button works,
  refresh works, deep-links work in packaged mode via `createHashRouter`.

### 2.3 [NIT] `frontend/src/views/MyRig.jsx.bak{,2,3,5,6}` numbering skips `.bak4`

`find frontend/src -name '*.bak*' | grep MyRig` returns the 5/6 sequence
without 4. Confirms these aren't manual backups — they're tool output
that occasionally skips a number. Just delete them all (and the rest
of the `.bak*` files; covered by yesterday's audit §5.1).

### 2.4 [NIT] Service layer naming is unusually regular — keep it

`grep -h '^def ' backend/services/*.py | grep -v '^def _'` shows 60
public functions with this pattern:

| Pattern | Example |
|---|---|
| `create_<entity>` | `create_jump`, `create_dropzone`, `create_rig`, `create_jumper`... |
| `get_<entity>` | `get_jump`, `get_aad`, `get_main`... |
| `list_<entity>` (plural) | `list_jumps`, `list_dropzones`, `list_people`... |
| `update_<entity>` | `update_jump`, `update_dropzone`... |
| `delete_<entity>` | `delete_jump`, `delete_main`... |
| Sub-collection ops | `add_<kind>_to_jumper`, `delete_<kind>_from_jumper` |

The credential sub-collection naming (`add_X_to_jumper`) is the *only*
intentional deviation, and it's justified — these aren't standalone
entities. This consistency is a real strength worth advertising in the
CONTRIBUTING.md.

### 2.5 [NIT] Model naming is regular too

`grep -h '^class ' backend/models/*.py | sort` shows the pattern:

| Read | Create | Update | List projection |
|---|---|---|---|
| `Jump` | `JumpCreate` | `JumpUpdate` | `JumpSummary` |
| `Dropzone` | `DropzoneCreate` | `DropzoneUpdate` | `DropzoneSummary` |
| `Rig` | `RigCreate` | `RigUpdate` | `RigSummary` |
| `Person` | `PersonCreate` | `PersonUpdate` | `PersonSummary` |
| `Jumper` | `JumperCreate` | `JumperUpdate` | (none) |
| AAD/Main/Reserve/Container | `…Create` | `…Update` | (none) |

Enums are all in `StrEnum`. Components inherit from `ComponentBase`.
The shape across 60+ classes is strictly uniform. **This is rare and
worth keeping.** The model-field-duplication finding from yesterday's
audit (§3.1) and the `JumpBase` inheritance pattern from the FastAPI
research deliverable (§3) are about *inner* duplication, not naming —
the naming is already correct.

### 2.6 [P3] Acronym capitalization: `Cop` vs `AAD`

Two acronyms appear in the model names: `AAD` (Automatic Activation
Device) is fully capitalized; `Cop` (Certificate of Proficiency) is
PascalCase. Python's PEP 8 traditionally PascalCases ("HttpRequest" not
"HTTPRequest" — see [PEP 8 Acronyms](https://peps.python.org/pep-0008/)),
but `AAD` is so universally written all-caps in the skydiving domain
that the all-caps form is clearly right. Possible drift: pick one rule
and apply uniformly. P3 — neither breaks anything, both are readable.

---

## Part 3 — Public-facing repo audit

### 3.1 README — needs a meaningful rewrite

Current state (6,232 bytes): developer-facing, technical, no hero
visual, no badges, no features bulleted list, no roadmap, no pointer
to CONTRIBUTING / SECURITY. Reads like internal docs that landed in a
public location.

The 2026 consensus structure (across
[awesome-readme](https://github.com/matiassingers/awesome-readme),
[makeareadme.com](https://www.makeareadme.com/), and the CFPB / Charm /
Datasette exemplars):

```
1. Name + one-line tagline (what + for whom + so-what)
2. Hero animated GIF or annotated screenshot
3. ≤4 badges: CI status | License | Python supported | Latest release
4. Why this exists (the "another?" paragraph)
5. Features (3-7 bullets, not paragraphs)
6. Install / Quickstart (copy-pasteable, per platform)
7. Usage (minimal walkthrough with output)
8. How your data is stored (XML+XSD pitch — your unique value)
9. Architecture link → ARCHITECTURE.md (one paragraph in README)
10. Roadmap / status (especially crucial for pre-alpha)
11. Contributing pointer → CONTRIBUTING.md
12. Security pointer → SECURITY.md
13. License
```

The single biggest first-impression lift is **the hero visual**. A
~5–10 second animated GIF (Kap on macOS, LICEcap on Windows, Peek on
Linux) showing the Jumps Log + Log Jump modal at <2 MB,
≤1200 px wide, stored under `docs/assets/` and referenced with a relative
path. Every exemplar repo leads with one.

### 3.2 Missing CONTRIBUTING.md

Recommendation: ~150 lines, honest about pre-alpha status, plus the
test+lint gate (per CLAUDE.md §7), commit conventions, **and a callout
of the DECISIONS.md discipline** (it's unusually good and worth
advertising as a project signal). Template:
[nayafia/contributing-template](https://github.com/nayafia/contributing-template/blob/master/CONTRIBUTING-template.md).

### 3.3 Missing SECURITY.md

Yes, even for a loopback app. GitHub's
[private vulnerability reporting docs](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configuring-private-vulnerability-reporting-for-a-repository)
require a SECURITY.md to enable the "Report a vulnerability" button.

Right size (~80 lines): plain-language threat model (single-user,
loopback-only per D48; files in the logbook folder are trusted, files
from elsewhere are not; XXE / path-traversal / zip-slip in attachment
imports are the realistic concerns and your D2 hardened parser + D4
sanitization are the mitigations); supported versions table; how to
report (GitHub PVR preferred, fallback email); expected response time
(be honest — single maintainer, best-effort, 7–14 days); out of scope.

Good reference shape: [standard/.github SECURITY.md](https://github.com/standard/.github/blob/master/SECURITY.md).

### 3.4 Missing CODE_OF_CONDUCT.md — and use v3, not 2.1

**Important correction** to my previous mental model: Contributor
Covenant 3.0 [was released July 28 2025](https://ethicalsource.dev/blog/contributor-covenant-3/),
not 2.1. Django adopted v3 in April 2026, Hanami in September 2025. v3
is less US-centric and replaces the enforcement ladder with
"Addressing and Repairing Harm." Adopt v3 via the
[official builder](https://www.contributor-covenant.org/version/) and
fill in a real reporting contact — shipping the template with the
placeholder email signals "copied without reading."

### 3.5 Missing `.github/` templates

Per [GitHub Docs on issue forms](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms),
YAML issue forms with required fields produce dramatically more
actionable reports than markdown. Minimum-viable set:

```
.github/
├── ISSUE_TEMPLATE/
│   ├── bug_report.yml      # required: version, OS, Python, repro steps
│   ├── feature_request.yml  # what problem, who hits it, alternatives
│   └── config.yml           # blank_issues_enabled: false; security link
└── pull_request_template.md # summary, linked issue, tests, DECISIONS,
                             # pytest+ruff+pyright green checkbox
```

### 3.6 DECISIONS.md at 342 KB → split to `docs/adr/NNNN-*.md`

This is the biggest structural deviation from current consensus. The
2025–2026 norm across [adr.github.io](https://adr.github.io/),
[MADR](https://adr.github.io/madr/),
[log4brains](https://github.com/thomvaill/log4brains), and Spotify's
adr-tools is **one file per decision** under `docs/adr/`. 342 KB in one
file:
- renders slowly in GitHub's markdown viewer,
- can't be cross-linked from code comments to a specific decision,
- is unscannable for a new contributor.

**Migration shape:**
- Keep `DECISIONS.md` as a slim **index** at the top level: D-number →
  title → status → relative link.
- Move each entry to `docs/adr/0001-xml-source-of-truth.md`,
  `docs/adr/0002-...`, etc. Lowercase kebab-case.
- Add a `Status:` field per file: `Accepted`, `Proposed` (your DRAFT
  markers), `Superseded by NNNN`, `Deferred`.
- Keep "deferred non-decisions" as their own ADR with `Status: Deferred`
  rather than burying them at the end of one giant file.
- CLAUDE.md's "quote the D-entry number in code comments" rule still
  works — just link to the file.

Cost: ~4-6 hours mechanical work (62 entries). Payoff: a contributor
can read one ADR and understand it in 60 seconds; before, they had to
ctrl-F through 342 KB. Schedule this for **before** the public push if
at all possible — first impression matters.

### 3.7 Files that probably shouldn't ship to public on day one

| File | Action | Why |
|---|---|---|
| `HANDOFF.md` (12 KB) | Move to `docs/internal/` or `.local/` | Internal session-to-session notes; reading "the prior agent's sandbox couldn't delete the venvs" is a confusing first impression |
| `reviews/2026-04-29-progress.html`, `2026-04-29-tech-debt-audit.html`, `2026-04-30-progress.html` | Move or `.gitignore` | Rendered audit reports; the `.md` versions are the source. Shipping the HTML reads as "build artifacts committed" |
| `ui-mockup.html` (57 KB) at root | Move to `docs/mockups/` or `mockups/` | Top-level non-source HTML reads as "abandoned scaffolding" |
| `pytest-cache-files-2_wpbl_s/` (empty dir at root) | Delete + verify gitignore | Stray name reads as "accidental commit" |
| `CLAUDE.md` | **Keep**, optionally rename to `AGENTS.md` | [agents.md](https://agents.md/) is emerging as a tool-agnostic convention. Yours is genuinely good; advertising AI-collab norms is no longer a stigma. |

### 3.8 Plus everything from yesterday's audit §5

`.bak*` cleanup, vitest gitignore patch, vite/vitest timestamps, `.venv*`
consolidation, `.DS_Store` removal. All P1, all an afternoon. Cited
there with full file:line evidence.

---

## Part 4 — Structural coherence (the "split big files" axis)

From yesterday's audit, three files dominate:

| File | LOC | Right shape |
|---|---|---|
| `backend/xml/serialize.py` | 1,394 | Split into `backend/xml/serialize/{__init__.py, _helpers.py, jump.py, dropzone.py, person.py, component.py (Main/Reserve/AAD/Container shared), rig.py, jumper.py, rig_snapshot.py}`. Re-exports preserve the import contract. Each file is 100-300 LOC and matches one mental model. |
| `backend/services/jump_service.py` | 1,349 | Split into `jump_service.py` (CRUD: create/get/list/update/delete) + `jump_attachment_service.py` (track/add/delete attachments) + `jump_files_service.py` (list_jump_files, the read-only walk). |
| `backend/services/rig_service.py` | 1,362 | Same shape: `rig_service.py` (CRUD) + `rig_assignment_service.py` (component assignment + swap rules per D37). |
| `frontend/src/modals/LogJumpModal.jsx` | 2,353 | Reducer-first → sub-components → `react-hook-form` + `zod`. See research deliverable §6 for the staged plan. |

For services, splitting also makes the writer-lock policy more
readable: every public function in the CRUD module is `@with_writer_lock`;
the attachment module's locking is per-operation. Today it's all
co-located in one 1,349-LOC file.

For models: yesterday's §3.1 (Pydantic field triple-duplication) maps
to the FastAPI research deliverable's recommendation — a `JumpBase`
inheritance pattern matching
[fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices)
and the
[full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template).
Lift the shared 16 fields into `JumpBase`; `JumpCreate(JumpBase)`,
`JumpUpdate(JumpBase)` (with fields marked optional via
`Field(default=...)` where needed), `Jump(JumpBase)` adds `id`, server
timestamps, and `attachments`.

Same shape for the per-component model families (AAD / Main / Reserve /
Container) and for Dropzone, Person, Jumper, Rig.

---

## Part 5 — Roadmap to "ready for GitHub"

Nine phases. Each ships independently with `pytest` + `ruff` + `pyright`
+ `vitest` green. Phases 1–3 are the **minimum bar to push public.**
Phases 4–9 are post-launch polish but improve the codebase regardless.

### Phase 1 — Repo hygiene (1 commit, ~1 hour)

**Goal:** working tree clean, gitignore complete.

- Delete: 55 `.bak*` files, 79 vitest timestamps, 43 vite timestamps,
  `.DS_Store` (x2), 6 inert frontend test stubs, `pytest-cache-files-*/`.
- Extend `.gitignore`: `*.bak*`, `frontend/vitest.config.*.timestamp-*.mjs`,
  `pytest-cache-files-*/`.
- Move `HANDOFF.md` → `docs/internal/session-handoffs/2026-04-30.md`
  (or `.local/`).
- Move `reviews/*.html` → `docs/reviews/` or `.gitignore`.
- Move `ui-mockup.html` → `docs/mockups/`.
- Consolidate `.venv*` to single `.venv/` (user-side `rm -rf` — INFRA-8).

### Phase 2 — Doc-truth alignment (1 commit, ~30 min)

**Goal:** every docstring matches current code.

- Fix `backend/services/_write_lock.py` docstring to acknowledge that
  reads-that-may-write-via-reconcile DO acquire the lock.
- Fix `backend/services/stats_service.py` docstring (deprecate the
  "fields aren't indexed" claim) — but only if Phase 4 isn't ready
  yet; otherwise the docstring change is part of the rewrite.
- Document the `_load_schema` LRU-cache lifetime in
  `backend/xml/validator.py` (yesterday's audit §1.3).
- Replace `__import__("sys")` with module-top `import sys` in
  `backend/api/rest.py:131,135`.

### Phase 3 — Public-facing files (1 commit, ~1 day)

**Goal:** repo is welcoming to a stranger landing on the GitHub page.

- Rewrite `README.md` to the 13-section consensus structure (Part 3.1).
- Capture and embed a hero animated GIF in `docs/assets/`.
- Write `CONTRIBUTING.md` (~150 lines, pre-alpha-honest, advertises the
  DECISIONS.md discipline).
- Write `SECURITY.md` (~80 lines, plain-language threat model).
- Write `CODE_OF_CONDUCT.md` (Contributor Covenant **3.0**).
- Add `.github/ISSUE_TEMPLATE/{bug_report.yml,feature_request.yml,config.yml}`
  and `.github/pull_request_template.md`.
- Add 4 badges to README header: CI status, MIT license, Python 3.11+,
  pre-alpha status.
- Enable private vulnerability reporting in repo settings.

**This is the minimum to push public confidently.**

### Phase 4 — DECISIONS.md split (1 commit, ~half a day)

**Goal:** ADRs follow 2025-2026 industry consensus shape.

- Create `docs/adr/`.
- Migrate D1-D62 to `docs/adr/0001-xml-source-of-truth.md` ...
  `docs/adr/0062-verify-trash-skip.md`. Kebab-case titles.
- Add `Status:` line to each (Accepted / Proposed / Deferred / Superseded
  by NNNN).
- Reduce `DECISIONS.md` to a slim index (D-number → title → status → link).
- Update CLAUDE.md §4 to point at the new location.
- Update code comments that cite `D26 §Mechanics` to use specific
  ADR file links (search-replace).

Cost is mechanical; can be done by an agent given a clear brief.
Schedule **before** the public push if at all possible.

### Phase 5 — Performance fix: stats from SQL, not XML walk (1 commit, ~3 hours)

**Goal:** close the new P2 finding.

- Rewrite `backend/services/stats_service.compute_stats` to use a single
  SQL aggregate against the v4+ index columns
  (`aircraft`, `discipline`, `freefall_time_s`, `date`, `dropzone`).
- Add test: with N jumps in the logbook, `compute_stats` performs zero
  `xml_parse` calls (assert via monkeypatch counter).
- Update the docstring to reflect the new pattern.
- Verify behaviour parity against the old implementation on a fixtured
  logbook.

### Phase 6 — Pydantic model deduplication (1 commit per entity, ~half a day total)

**Goal:** end the triple-duplication. Match
[full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)
shape.

- For each of: `Jump`, `Dropzone`, `Person`, `Jumper`, `Rig`, `AAD`,
  `Main`, `Reserve`, `Container`:
  - Extract shared fields into `XBase`.
  - `XCreate(XBase)`, `XUpdate(XBase)` (with optional overrides),
    `X(XBase)` adds `id`/timestamps/attachments.
- Add a test that asserts the three classes' fields agree where they
  should (regression net for future field adds).

### Phase 7 — Service module split (1 commit per service, ~1-2 days)

**Goal:** no service file > 800 LOC.

- Split `jump_service.py` (1,349) → `jump_service.py` (CRUD) +
  `jump_attachment_service.py`.
- Split `rig_service.py` (1,362) → `rig_service.py` (CRUD) +
  `rig_assignment_service.py`.
- Split `xml/serialize.py` (1,394) → `xml/serialize/__init__.py` +
  per-entity submodules with re-exports.

### Phase 8 — Frontend modernization wave 1 (multi-commit, ~3-5 days)

**Goal:** unblock TypeScript + typed API client.

- Enable `allowJs + checkJs` in `tsconfig.json`; ship with no strict
  flags initially.
- Convert `api.js` → `api/index.ts` + per-domain `api/jumps.ts`,
  `api/rigs.ts`, etc. Use `openapi-typescript` for types,
  `openapi-fetch` for calls. Add `npm run openapi:generate` script.
- Convert `units.js`, `lineTypes.js`, `rigShape.js` → TypeScript
  (small, low-risk).
- Adopt `react-router` v7 (`createHashRouter`) for view dispatch;
  modal open-state in search params; `App.jsx` becomes a route layout.

### Phase 9 — Frontend modernization wave 2 (multi-commit, ~1 week)

**Goal:** dismantle the 2,353-LOC modal; raise the component-quality
floor.

- Adopt `shadcn/ui`: `npx shadcn add dialog button select input textarea
  date-picker checkbox label`. Replace raw Tailwind primitives
  incrementally.
- Convert `LogJumpModal.jsx` to TypeScript; consolidate 45 hooks into
  one `useReducer`; extract `<JumpHeader>`, `<EquipmentPicker>`,
  `<FreefallDetails>`, `<NotesAndGroup>` sub-components.
- Migrate the reducer to `react-hook-form` + `zodResolver`.
- Same shape for the next biggest modals: `ComponentDetailModal.jsx`
  (1,472), then `AddRigModal.jsx`, `AddComponentModal.jsx`.
- Add per-modal smoke tests mirroring `views.smoke.test.jsx`.
- Add TanStack Query for server state once API is typed; remove the
  `cache: 'no-store'` defensive flag (server adds `Cache-Control:
  no-store` header instead).

---

## Bottom line

**The architecture under the hood is strong** — five of six pressure-tested
backend choices match or beat 2026 consensus, the security and
durability surfaces are exemplary, the test discipline is real. **The
public-facing surface and the frontend stack are what stand between the
project and "at its best."**

If you ship only Phases 1–3 before going public, the repo will look
materially better to a stranger landing on it than today, with no
behavior change anywhere. Phase 4 (DECISIONS.md split) is the highest-
leverage *structural* change to fit in before launch — afterwards it
gets harder because every D-entry citation in every code comment needs
updating.

Phases 5–9 are improvements regardless of launch timing. None of them
require revisiting an existing D-entry contract.

---

## Sources used in this review

### Storage format & XSD
- [Library of Congress Recommended Formats Statement — Datasets](https://www.loc.gov/preservation/resources/rfs/data.html)
- [LoC RFS 2025–2026 PDF](https://www.loc.gov/preservation/resources/rfs/RFS%202025-2026.pdf)
- [SQLite as LoC Recommended Storage Format (since 2018-05-29, still current)](https://sqlite.org/locrsf.html)
- [JSON Schema Draft 2020-12 spec](https://json-schema.org/draft/2020-12)
- [JSON Schema key/uniqueness gap discussion (open since 2019)](https://github.com/json-schema-org/json-schema-vocabularies/issues/22)
- [Definitive XML Schema — Identity Constraints (xs:key/keyref/unique)](http://www.datypic.com/books/defxmlschema/chapter17.html)
- [Schematron — ISO/IEC 19757-3:2025](https://en.wikipedia.org/wiki/Schematron)
- [Zotero data directory documentation](https://www.zotero.org/support/zotero_data)
- [Logseq DB-version rationale (moving from markdown to DB)](https://github.com/logseq/docs/blob/master/db-version.md)
- [Hitchdev: What's wrong with TOML](https://hitchdev.com/strictyaml/why-not/toml/)

### Desktop framework
- [pywebview repo](https://github.com/r0x0r/pywebview/)
- [pywebview 6.0 release blog (Aug 2025)](https://pywebview.flowrl.com/blog/pywebview6)
- [Tauri v2 stable docs](https://v2.tauri.app/)
- [Tauri v2 sidecar pattern docs](https://v2.tauri.app/develop/sidecar/)
- [Tauri v2 Python sidecar example repo](https://github.com/dieharders/example-tauri-v2-python-server-sidecar)
- [Tauri issue #8139 — sidecar process management](https://github.com/tauri-apps/tauri/issues/8139)
- [Tauri issue #4134 — no sidecar hot-reload](https://github.com/tauri-apps/tauri/issues/4134)
- [Tauri issue #10649 — NSIS + PyInstaller AV false positives](https://github.com/tauri-apps/tauri/issues/10649)
- [Microsoft Edge/WebView2 Win10 support to Oct 2028](https://www.windowslatest.com/2025/08/10/microsoft-wont-end-support-for-chromium-edge-and-pwas-on-windows-10-until-october-2028/)
- [WebView2 distribution docs](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [Electron releases](https://github.com/electron/electron/releases/)
- [endoflife.date / Electron](https://endoflife.date/electron)

### FastAPI & Pydantic
- [Concurrency and async / await — FastAPI docs](https://fastapi.tiangolo.com/async/)
- [FastAPI SQL tutorial](https://fastapi.tiangolo.com/tutorial/sql-databases/)
- [Handling Errors — FastAPI docs](https://fastapi.tiangolo.com/tutorial/handling-errors/)
- [zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices)
- [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)
- [Pydantic Models concepts](https://docs.pydantic.dev/latest/concepts/models/)
- [Pydantic partial-update models (Orchestra)](https://www.getorchestra.io/guides/pydantic-partial-update-models-in-fastapi-a-tutorial)
- [RFC 9457 — Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html)
- [fastapi-problem-details](https://pypi.org/project/fastapi-problem-details/)
- [Meta's Typed Python in 2024 survey](https://engineering.fb.com/2024/12/09/developer-tools/typed-python-2024-survey-meta/)
- [Pyright configuration docs](https://github.com/microsoft/pyright/blob/main/docs/configuration.md)
- [Basedpyright reference](https://pydevtools.com/handbook/reference/basedpyright/)

### Frontend (TypeScript, components, forms)
- [State of JavaScript 2024 — Usage](https://2024.stateofjs.com/en-US/usage/)
- [JS & TS Trends 2024 — JetBrains/WebStorm](https://blog.jetbrains.com/webstorm/2024/02/js-and-ts-trends-2024/)
- [Orval vs openapi-typescript vs Kubb — PkgPulse 2026](https://www.pkgpulse.com/guides/orval-vs-openapi-typescript-vs-kubb-openapi-client-2026)
- [openapi-typescript on GitHub](https://github.com/openapi-ts/openapi-typescript)
- [Composable Form Handling 2025 — Makers' Den](https://makersden.io/blog/composable-form-handling-in-2025-react-hook-form-tanstack-form-and-beyond)
- [UI Component Libraries, shadcn/ui, and the Revenge of Copypasta — RedMonk Apr 2025](https://redmonk.com/kholterhoff/2025/04/22/ui-component-libraries-shadcn-ui-and-the-revenge-of-copypasta/)
- [State Management in 2025 — Makers' Den](https://makersden.io/blog/react-state-management-in-2025)
- [useState vs useReducer vs XState, Part 1: Modals — Matt Pocock / Stately](https://stately.ai/blog/2021-07-28-usestate-vs-usereducer-vs-xstate-part-1-modals)
- [TanStack Router vs React Router v7 — PkgPulse 2026](https://www.pkgpulse.com/blog/tanstack-router-vs-react-router-v7-2026)

### Repo presentation
- [GitHub Docs — Community profiles for public repositories](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories)
- [GitHub Docs — Adding a security policy](https://docs.github.com/en/code-security/getting-started/adding-a-security-policy-to-your-repository)
- [GitHub Docs — Configuring private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configuring-private-vulnerability-reporting-for-a-repository)
- [GitHub Docs — Syntax for issue forms](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms)
- [Contributor Covenant 3.0 announcement (Jul 28 2025)](https://ethicalsource.dev/blog/contributor-covenant-3/)
- [Contributor Covenant — Versions page](https://www.contributor-covenant.org/version/)
- [matiassingers/awesome-readme](https://github.com/matiassingers/awesome-readme)
- [makeareadme.com](https://www.makeareadme.com/)
- [adr.github.io — Architecture Decision Records](https://adr.github.io/)
- [MADR](https://adr.github.io/madr/)
- [agents.md (tool-agnostic agent-config convention)](https://agents.md/)
- [nayafia/contributing-template](https://github.com/nayafia/contributing-template/blob/master/CONTRIBUTING-template.md)
- [standard/.github SECURITY.md — small-project exemplar](https://github.com/standard/.github/blob/master/SECURITY.md)

*— end —*

# Contributing to Skydive Logbook

Thanks for stopping by. This project is **pre-alpha** with a **single
maintainer** — please read this whole file before opening an issue or
a PR. It's short, it sets expectations honestly, and it tells you the
two things that matter most.

## Status before you decide to contribute

- v0.1 is still being shipped. Breaking changes happen and are
  documented in `DECISIONS.md`.
- Releases are best-effort, not on a schedule.
- The maintainer triages issues weekly at best. Response within 7 days
  is the goal, not a guarantee.
- Big PRs are likely to bounce. Small, focused PRs against an open
  issue (or proposed in a new one first) are the path that lands.

If that doesn't match your timeline, that's fine — fork it, use it,
ship something. The MIT license means you owe nothing back.

## How to report something

| You want to                                  | Do this                                                                                                          |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Report a bug                                 | Open a [Bug report](../../issues/new?template=bug_report.yml).                                                   |
| Propose a feature                            | Open a [Feature request](../../issues/new?template=feature_request.yml) — read the scope-discipline note below first. |
| Report a security vulnerability              | **Do NOT open a public issue.** See [SECURITY.md](SECURITY.md).                                                  |
| Ask a question                               | Issues aren't the right place. Open a [Discussion](../../discussions) instead.                                   |

## Two things that matter most

### 1. Read `DECISIONS.md` before touching anything cross-cutting

This project is unusual in that the **reasoning** behind every
load-bearing choice — XML on disk, SQLite as a rebuildable index, the
RFC 9457 error envelope, atomic writes, the writer-lock policy — is
written down as a numbered D-entry in `DECISIONS.md`.

Before changing storage format, error shape, API surface, concurrency
model, or anything similarly cross-cutting:

1. Search `DECISIONS.md` for a relevant `D<N>` entry.
2. If one exists, honour it. If the decision is wrong, supersede it
   with a new D-entry — don't edit the old one in place.
3. If none exists and the choice is non-obvious, draft one before
   coding. Even a terse decision note beats silent drift.

This is the project's most important discipline. It is the reason a
single maintainer has been able to evolve the code over many phases
without losing track of why things are the way they are. Honour it.

### 2. Ship in small, logical, verified pieces

Each PR should:

- touch as few files as possible for a coherent outcome
- ship with tests that actually exercise the new behaviour
- run `pytest` and `ruff check` green before being marked ready
- run `pyright` green (we're on strict for production code per D51)
- update the relevant D-entry (or add one) if it shifts a contract

If you find yourself wanting to land a single 30-file PR, please open
an issue describing the plan first. Big PRs are not faster — they are
slower to review and more likely to be rejected.

## Dev setup

You need:

- Python 3.11 or newer
- [uv](https://github.com/astral-sh/uv) (`pip install uv` if you don't
  have it — it manages the venv and the lockfile)
- Node.js 20+ (only if you're touching the frontend)

```bash
# clone, install, run tests
git clone https://github.com/<your-fork>/skydive-logbook
cd skydive-logbook
uv sync --extra dev
uv run pytest backend/tests
uv run ruff check backend
uv run pyright backend

# frontend (optional)
cd frontend && npm install && npm test && cd ..
```

`uv sync` reads `uv.lock` and installs the locked dependency versions
across Python 3.11 / 3.12 / 3.13 × Ubuntu / macOS / Windows — the same
matrix that CI runs.

## What "done" looks like

Per `CLAUDE.md` §7, a change isn't done until all of these pass:

```bash
uv run ruff check backend
uv run pyright backend
uv run pytest backend/tests
(cd frontend && npm test)   # if frontend is touched
```

CI runs the same triple plus the Vitest job. A red CI is not "an
intermittent flake" until you've reproduced it locally — please don't
re-trigger CI hoping it goes green.

### Test posture

- Real `tmp_path` over mocks for storage tests. Mocking filesystem
  behaviour hides cross-platform bugs (see `CLAUDE.md` §7).
- Integration over unit where the integration is cheap.
- Crash-path tests (subprocess + SIGKILL) for any new multi-file write
  — disk XML is the source of truth, so a half-written sequence is the
  state that has to be recoverable.

### Lint and type

- `ruff` with the rule set in `pyproject.toml` (`E F W I UP B SIM`,
  `E501` ignored).
- `pyright` strict on production code, basic on `backend/tests/` and
  `backend/xml/`. The rationale is in `pyproject.toml`'s
  `[tool.pyright]` block; please read it before disabling a rule.

## Commit conventions

- Imperative mood, ≤72 chars on the first line. Body lines wrap at 80.
- Reference the issue you're closing: `Closes #N` or `Refs #N`.
- If the change shifts a D-entry, mention the D-number:
  `Refs D50 — clarify writer-lock policy`.

## Code style

- Imports at module top, not inside functions (unless circular
  import). Ruff's `I` rule enforces the standard order.
- f-strings preferred over `%` and `.format()`.
- Match the surrounding code's voice and comment density. The
  storage and observability modules are heavily commented for a
  reason; mirror that bar in new code that touches those domains.
- Docstrings cite the relevant `D<N>` when the shape of the code is
  non-obvious from the implementation alone.

## Out of scope for v0.1

These are **binding non-decisions** from `DECISIONS.md` — do not pull
them into scope without an explicit scope change from the maintainer:

- FlySight parsing (just store the file)
- Digital signature enforcement (D6 reserves the element, doesn't enforce)
- Multi-user accounts
- Import from other logbook apps
- Video thumbnails
- A mobile app
- A headless-server deployment story
- Auto-update

A PR adding any of the above will be closed as out-of-scope.

## License

By submitting a contribution you agree your work is offered under the
project's [MIT license](LICENSE). There's no CLA.

## Code of Conduct

Participation in this project is governed by the
[Contributor Covenant 3.0](CODE_OF_CONDUCT.md). Be kind.

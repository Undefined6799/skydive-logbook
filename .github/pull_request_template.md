<!-- Thanks for sending this. Please fill in every section before
     marking the PR ready — the checklist is the contract. -->

## Summary

<!-- One paragraph: what does this PR do, and why? -->

## Linked issue

Closes # <!-- or "Refs #N" if not closing -->

## Changes

<!-- A short bulleted list of the meaningful changes. Skip housekeeping
     (lint fixes, comments) unless they're load-bearing. -->

-

## Decision-record impact

<!-- Per CONTRIBUTING.md: any cross-cutting change should cite or
     create a D-entry. Mark one: -->

- [ ] No D-entry is affected.
- [ ] An existing D-entry covers this — referenced inline:
  `D<N>` …
- [ ] This introduces a new D-entry — added at `DECISIONS.md` D<N>
  ("…").
- [ ] This supersedes an earlier D-entry — `D<N>` marked as
  "Superseded by D<M>".

## Verification

<!-- Per CLAUDE.md §7, the green-light triple must pass before this is
     mergeable. Confirm: -->

- [ ] `uv run ruff check backend` — clean
- [ ] `uv run pyright backend` — 0 errors, 0 warnings
- [ ] `uv run pytest backend/tests` — green
- [ ] `(cd frontend && npm test)` — green (only if frontend touched)
- [ ] New behaviour has new tests, OR this PR is documentation-only.

## Backwards compatibility

<!-- Did you change anything a third-party API consumer would see?
     The XSD namespace, an error code, a URL shape, a response field? -->

- [ ] No public-API change.
- [ ] Additive only (new field, new endpoint, new error code).
- [ ] Breaking — bumped XML schema namespace and/or REST API version,
  with the rationale documented in the D-entry above.

## Anything else reviewers should know

<!-- Tricky edge cases, follow-up PRs you're planning, things you
     considered and rejected, screenshots of UI changes. -->

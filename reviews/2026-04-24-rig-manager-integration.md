# Rig Manager — integration analysis

Author: backend pair (Claude)
Date: 2026-04-24
Status: analysis for scoping discussion — no code changes proposed yet

This doc maps the externally-authored "Rig Manager" spec (the handoff
shared 2026-04-24) against the logbook's current architecture. It is
grounded in `DECISIONS.md` D1–D32, `ARCHITECTURE.md`, and the code as
of Phase 3.6. It deliberately does **not** propose an implementation —
it flags scope, surfaces architectural fit and friction, and proposes
a first phase if Alex wants to proceed.

---

## 1. TL;DR

The rig-manager spec is a much larger feature than the "equipment
tracking" line in D14. About 80% of it is pure addition that fits the
project's XML-as-truth posture cleanly. About 20% — specifically §1.2
(auto-increment on jump log) and the per-kind data model — fights two
load-bearing decisions (D22, D25) and needs an explicit scope change
plus new D-entries before any code moves.

My recommendation: finish v0.1 per D14 with the existing thin
`Equipment` model, then scope the rig manager as a post-v0.1 module
with its own D-entries and a 5–6 phase plan mirroring the Phase 3.x
jumps rollout. The first phase is strictly the static data model —
no jump integration, no counters, no status dots.

---

## 2. Scope check against D14

D14 pins v0.1 equipment tracking to this single sentence:

> Add/edit containers, canopies, and AADs as separate entities. Link
> them to jumps by reference. Track reserve repack dates and AAD
> service intervals.

The existing `backend/models/equipment.py` covers that with one
`Equipment` model, a closed `EquipmentKind` enum (`container | canopy |
reserve | aad`, D22), plus `last_reserve_repack` and `aad_service_due`
as kind-specific optional fields. The jump XML has `EquipmentRefs`
(container_id, canopy_id, reserve_id, aad_id). That is the entire v0.1
equipment surface.

The rig-manager spec introduces:

- 7 distinct entity kinds — Jumper, Rig, Main, Lineset, Reserve, AAD,
  Container — with per-kind fields that don't live on a common shape
  (`default_environment_flags`, `recert_extensions[]`, `mode`,
  `current_lineset`, `repack_limit`, `jurisdiction`, `notes_log[]`).
- An event model (repacks, relines, main swaps, jump-as-wear-event).
- Formulas (wingloading, JYRO lineset consumption, AAD mode
  recommendation) and derived status-dot logic (green/yellow/red with
  90% / ±6-month thresholds).
- USPA/CSPA dual jurisdiction with a 180d/270d repack clock.
- Auto-increment side effects on every jump save (§1.2).

None of this is derivable from "add/edit containers, canopies, AADs +
repack date". Per CLAUDE.md §10 (scope) and the D14 consequences:

> Any request that drifts outside this list — even a sensible one —
> needs an explicit scope change from Alex before you spend effort on
> it.

So the first-order question is **not** "how do we integrate it" — it is
"is this inside v0.1 or outside it?". Answering that is a decision,
not an implementation choice. My read is it should be outside v0.1,
for three reasons: size (~6 phases of work), contract-impact (new
XSDs, a new D-entry replacing/extending D22, and new error codes), and
the cadence rule that says we ship small slices.

---

## 3. Where the spec fits the architecture cleanly

A lot of the spec lands well on what we already have.

**Entities-as-XML-files extends naturally.** Components, rigs, and
events are all just more XML files, each with its own XSD, written
through `atomic_write` and listed in a SHA256SUMS manifest just like
`jump.xml` is today. The hardened parser and the validator layer don't
need changes — they already operate generically on "validate XML
document against declared-namespace XSD".

**The rig manager explicitly declares no upstream coupling to the
logbook (§1.4).** The logbook never reads rig-manager state; only the
reverse. That means the integration surface is a small set of jump
fields (rig_id, environment_flags, reserve_ride) plus a small set of
derived-counter queries that the rig manager runs when rendering its
own views. That is the kind of one-way dependency that keeps D7 (thin
REST, logic in services) honest: a `services/rig_service.py` can read
from `jump_service.list_jumps(...)` without the reverse being true.

**Rides/fires are rigger-entered, not auto-derived (§1.3, §7.2).** The
spec explicitly refuses to auto-increment `reserve_ride_count` or
`aad_fire_count` from a jump with the respective flags, because the
rigger will re-enter them at repack time and we'd double-count. This
matches the project's existing discipline of not silently mutating
user data (see D23 refusing to auto-resolve duplicate jump_numbers).
The spec and the project agree: loud over clever.

**Formulas are pure functions.** Wingloading, JYRO consumption, AAD
mode recommendation, status computation — all take values in and
return values out. No I/O, no state. They slot into `backend/services/`
as testable helpers and compose into the status-dot pipeline. This is
exactly the shape D7 asks for.

**"Only current state" for component → rig assignment (§2.3).** The
spec specifically chooses not to track previous assignments. That
keeps the component XMLs simple and avoids the historical-timeline
problem that would otherwise force either an event log of assignments
or an immutable per-event XML.

---

## 4. Where the spec fights the architecture (and what to do)

### 4.1 D22 (closed EquipmentKind enum) vs per-kind models

D22 locks the on-disk `<kind>` to exactly four values and frames the
Pydantic side as a single `Equipment` model whose kind-specific fields
are optional on the shared shape. The rig-manager spec needs:

- A `Main` with `current_lineset: Lineset` (nested), `jump_count`,
  `default_environment_flags: list[EnvFlag]`, `notes_log: list[Note]`.
- A `Reserve` with `repack_limit`, `ride_limit`, `repack_count`,
  `ride_count`, `recert_extensions: list[RecertExtension]`.
- An `AAD` with `mode`, `is_changeable_mode`, `fire_count`, rules
  for service windows and EOL that depend on brand+model+DOM.
- A `Container` with `size`, `dom`, `jump_count`.
- A `Rig` composing four component IDs + `jurisdiction`.
- A `Jumper` with `exit_weight_lb`.

Putting all these optional fields on a single `Equipment` would make
the model incoherent — the meaningful-field combinations stop forming
a lattice. The right move is a tagged-union on disk: keep
`<kind>` closed but expand the XSD to define per-kind complexTypes
(`<main>`, `<reserve>`, `<aad>`, `<container>`, `<rig>`, `<jumper>`)
rather than one fat `<equipment>` with conditional fields.

This is a **new D-entry** superseding D22's Python-side assumption
while preserving D22's rationale (mistyped kinds must surface loudly
at write time). The XSD-side closed-enum stays; the Python side
becomes a discriminated union. Call the new entry roughly "D33 —
Per-kind equipment models under a closed discriminator."

### 4.2 D25 (single-folder crash table) vs §1.2 auto-increment

This is the single biggest architectural question in the spec.

D25's crash table describes exactly one write path: one `jump/` folder
gets `jump.xml`, attachments, `SHA256SUMS`, and optionally
`summary.md`. Every crash row assumes "this folder is the unit of
atomicity; nothing else on disk is touched." The recovery story
(`folder_reconcile`) is per-folder.

Spec §1.2 says that logging one jump must also increment:

- `main.jump_count`
- `main.current_lineset.consumed_lb`
- `aad.jump_count`
- `container.jump_count`

That is a mutation of three-or-four more XML files (depending on how
we represent the main/lineset pair). Doing this honestly means either
(a) a distributed multi-folder write with a new WAL-style recovery
story, or (b) accepting that on crash mid-write the counters drift
until a manual reconcile.

Both (a) and (b) are bad. There is a third option the spec doesn't
name but that fits this project better:

**Don't store the counters; derive them.**

- `main.jump_count` = `COUNT(jump) WHERE rig_id == r AND r.main_id
  was m at jump.date`. Computable from jumps alone given main-swap
  event history.
- `lineset.consumed_lb` = `SUM(cost_per_jump_lb FROM jump, environment
  multipliers) WHERE jump.date ∈ [install_date, now] AND rig_id was
  m's rig`.
- `aad.jump_count` / `container.jump_count` = analogous.

Counters become a **projection** into the SQLite index, rebuilt on
reindex from immutable XML (jumps + main-swap events + reline events
+ repack events). This keeps D3 intact (index rebuildable from XML)
and keeps D25 intact (jump-write is still a single-folder operation).
The hot path doesn't change; only the read path grows to hydrate
projections.

What this leaves un-derivable (must be stored): `reserve.repack_count`
(repack events — derivable from a `repacks/` event stream),
`reserve.ride_count` (rigger-entered at repack, belongs on the
repack-event XML), `reserve.recert_extensions` (entered by the user,
belongs on the reserve XML itself), `aad.fire_count` (same as
ride_count). All of these change only at rigger-visit time, which is
already not a hot path and can comfortably be a small multi-file
write inside a repack service function, with a documented crash
story in the new D-entry.

**Consequence of this choice.** The spec's §1.2 "side effects" table
is reinterpreted: the logbook does nothing special on jump save —
no counters mutate. The rig manager's views call
`derive_component_stats(rig_id, as_of)` and the numbers come back
fresh. This is a cleaner contract to implement and a cleaner one to
explain in docs, and it matches the project's "data outlives the app"
value: anyone with just `jumps/` and the component XMLs can recompute
every counter from first principles.

I think this is the right call, but it's an explicit departure from
the spec as written, so it gets a D-entry.

### 4.3 EquipmentRefs becomes awkward once rigs exist

`Jump.equipment: EquipmentRefs` currently has four nullable UUIDs.
The spec replaces this with `Jump.rig_id` (UUID of a rig) plus
`environment_flags`. EquipmentRefs becomes redundant in the rig-era
world: if you know the rig_id and the date, you can look up the rig's
composition at that time (current-state only, per §2.3) and recover
every component reference.

Two transition paths:

- **Coexist.** Keep EquipmentRefs optional; add optional `rig_id`
  and `environment_flags`. Old jumps reference components directly;
  new jumps reference the rig. Reads resolve either. Ugly but fully
  additive (stays in v1 per D18).
- **Supersede.** Mark EquipmentRefs deprecated, stop writing it,
  keep reading it for old XMLs. New writes always use rig_id.
  Cleaner but technically still within v1.

Both are options. "Supersede" has the better long-term story if the
rig manager is actually adopted.

### 4.4 XSD v1 vs v2

D18 says additive changes (new optional elements) stay in v1. The rig
manager's additions are *mostly* additive:

- New top-level elements (`<main>`, `<reserve>`, `<aad>`,
  `<container>`, `<rig>`, `<jumper>`) in separate files — additive.
- New top-level elements for event files (`<repack_event>`,
  `<reline_event>`, `<main_swap_event>`) — additive.
- New optional elements on `<jump>` (`<rig_id>`, `<environment_flags>`,
  `<reserve_ride>`) — additive.

The only arguably-breaking change is if we remove `<equipment>`'s
kind-specific fields (`last_reserve_repack`, `aad_service_due`)
because the new per-kind files own that state. Removing elements is
breaking per D18. Workaround: leave them as optional in v1 forever and
stop writing them; the XSD is deprecating-by-policy rather than by
schema. Any third-party tool reading old files still sees what it
expects.

Net: this can all land in SCHEMA.v1 without a namespace bump. Whether
it *should* is a judgment call — a v2 with a cleaner shape would be
the right move if we were adding this in year 3. At v0.1 + 1 day, the
cost of maintaining two namespaces outweighs the cleanliness benefit.

### 4.5 Jurisdiction logic (USPA/CSPA, 180/270 day)

Pure addition. `Rig.jurisdiction: Literal["USPA", "CSPA", "both"]`,
plus `next_repack_due: date` derived from `last_repack_date + cycle_days`.
Computed in services. No architectural friction.

### 4.6 Status dots, thresholds, and info nudges

All pure-function logic. The only design question is caching:

- **Compute on each read.** Simple, always-fresh, but allocates per
  request and recomputes time-dependent thresholds on every call.
- **Cache in SQLite.** Precompute on any write that changes input;
  invalidate on the clock (±6 month windows rotate nightly). More
  correct rendering speed, more moving parts.

For v0.1 of the rig manager, compute-on-read is simpler and almost
certainly fast enough for a single-user local app. Revisit only if
profiling shows dashboard rendering is slow.

---

## 5. Proposed folder layout extension

```
logbook_root/
  jumps/                      # unchanged
    [<N>] <title>/
  equipment/                  # existing, thin; deprecated-by-policy
  rigs/                       # new, one file per rig
    <uuid>.xml
  components/                 # new, per-kind subfolders
    mains/<uuid>.xml
    reserves/<uuid>.xml
    aads/<uuid>.xml
    containers/<uuid>.xml
  jumpers/                    # new, typically one file
    <uuid>.xml
  events/                     # new, append-only
    repacks/<YYYY-MM-DD>-<uuid>.xml
    relines/<YYYY-MM-DD>-<uuid>.xml
    main_swaps/<YYYY-MM-DD>-<uuid>.xml
  .trash/                     # unchanged
  SCHEMA.v1.xsd               # extended
  SHA256SUMS                  # (if we extend manifest coverage — TBD)
```

Open question: does `SHA256SUMS` live per-folder (as it does today for
jumps) or at logbook-root covering everything? The current design is
per-jump-folder because each jump is self-contained. For single-file
entities (`rigs/<uuid>.xml`), there's no folder to SHA. Simplest:
manifest-per-file-group, or skip the manifest for single-file
entities and rely on XSD + hardened parser for corruption detection.

---

## 6. Proposed D-entries to draft

Before any code lands, these decisions need to be recorded:

1. **D33 (hypothetical) — Rig manager scope and v0.2 positioning.**
   Pins the spec as a post-v0.1 module. Enumerates what is in and
   out. Lists the cross-cutting items that must be coordinated with
   D14 closeout.

2. **D34 — Per-kind equipment models under a closed discriminator.**
   Supersedes D22's Python-side assumption. Keeps the closed-enum
   rationale at the XSD boundary but expands the type surface.

3. **D35 — Component wear counters are derived, not stored.**
   Reinterprets rig-manager spec §1.2 for this project: jump-save
   has no side effects on component XMLs. Counters are an index
   projection over jumps + events.

4. **D36 — Event-sourced rigger state.** Repack, reline, and
   main-swap events are first-class immutable XML files. Reserve
   repack/ride counts, AAD fire counts, lineset history — all
   derivable from this stream.

5. **D37 — Rig-era jump schema additions.** `<rig_id>`,
   `<environment_flags>`, `<reserve_ride>` added to jump XSD v1 as
   optional elements. EquipmentRefs deprecated-by-policy (still read,
   not written).

6. **D38 — Status and wear projections in the SQLite index.**
   Formalizes what projections live where, rebuild rules, and
   reindex coverage.

Each is a small decision with independent rationale. They can land
one at a time as the module is built out.

---

## 7. Proposed first phase (if we proceed)

Following the working cadence rule — small logical slices, verified,
then propose the next — the first phase of a rig-manager module is
strictly about getting the static data model on disk. No jump
integration. No counters. No status dots. No formulas.

**Phase R.0 — Static rig/component/jumper entities, read-only**

- XSD extension: define `<rig>`, `<main>`, `<reserve>`, `<aad>`,
  `<container>`, `<jumper>` top-level elements. Additive within
  SCHEMA.v1.
- Pydantic models under `backend/models/`: split `equipment.py` (or
  retire it in favor of per-kind modules). New
  `models/rig.py`, `models/main.py`, `models/reserve.py`,
  `models/aad.py`, `models/container.py`, `models/jumper.py`.
- XML serialize/parse round-trip for each type.
- Storage layout: `components/{mains,reserves,aads,containers}/`
  and `rigs/`, `jumpers/`. `atomic_write` them; no SHA256SUMS yet
  (open question).
- Service layer: create + get for each type. No list, no update,
  no delete yet — those follow in R.1.
- Tests: round-trip, XSD validation, path-safety.

What R.0 explicitly does **not** touch: `jump.xml`, existing
`Equipment` model, index schema, any formula, any status, any event.

That's roughly the shape of Phase 3.0 for jumps, and it succeeded by
being that narrow. Expect the same here.

---

## 8. Open questions for Alex

Before anything moves:

1. **Scope call.** Is the rig manager in v0.1, pushed to v0.2, or
   declined? My read is v0.2. Your call.
2. **Spec §1.2 auto-increment — fidelity vs architecture.** Do we
   follow the spec literally (multi-folder atomic writes on jump
   save, new crash D-entry), or adopt the "derive, don't store"
   approach (cleaner fit, spec-divergent)?
3. **EquipmentRefs transition — coexist or supersede.** Both work;
   "supersede" is cleaner long-term.
4. **Manifest for single-file entities.** Do we extend
   `SHA256SUMS` to cover `rigs/`, `components/`, `events/`, or
   accept that XSD + hardened parser is enough corruption detection
   for those single-file entities?
5. **Jurisdictions.** USPA + CSPA only, or do we leave room for
   others (BPA, APF, etc.) by modeling jurisdiction as a more open
   enum from day one?
6. **Jumper singleton.** v0.1 is single-user (D8 default `"default"`).
   Is `Jumper` always-1? Or do we allow multiple profiles (tandem
   passenger, student tracking) while still having a `default_jumper`
   for own jumps?

---

## 9. What I am **not** recommending

- Implementing anything until a scope decision and the relevant
  D-entries are drafted.
- Mutating jump-save to touch component XMLs (per D25 + §4.2).
- Retiring D22 wholesale — its XSD-boundary discipline is still
  right; only the Python-side shape needs to evolve.
- Bumping SCHEMA.v2. Additive extension of v1 is sufficient.
- Delegating the data-model split to `api-contract-steward` or
  `backend-engineer` subagents before the scope and D-entries are
  settled. Agents without a decision-record to pin against will
  produce plausible-but-divergent output.

---

## 10. One-line next action

Decide scope (v0.1 / v0.2 / decline). If v0.2, drafting D33 is the
first piece of real work; everything else waits on it.

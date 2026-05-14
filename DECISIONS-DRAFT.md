# Decisions — Draft

In-flight decisions that haven't been ratified yet. Promoted to
`DECISIONS.md` when settled, or deleted if abandoned. Numbering follows
the same D-prefix sequence so a promoted draft keeps its number.

A draft has the same shape as a settled decision (**Decision**, **Why**,
**Consequences**, **Alternatives considered**) but carries a
``**Status:** DRAFT`` line near the top with the date of the last
revision and the gating question.

D52 was promoted to `DECISIONS.md` on 2026-05-14 with the v0.1.0-beta.1
release prep: the unsigned-binaries posture is now ratified rather than
draft.

---

## D55 — Hide/show built-in fields via settings.xml (DRAFT)

**Status:** DRAFT, 2026-04-30. Pending Alex review.

**Decision.** Add a singleton settings record at
`logbook_root/settings.xml` with a small initial shape:

- `hidden_fields` — list of strings naming jump fields the UI
  should hide in the log form, jump detail, and (where applicable)
  the list view. Each entry is one of a closed enum of *hideable*
  field names: `landing_distance_m`, `landing_direction`,
  `packed_by`, `group_size`, `group_members`, `aircraft`,
  `freefall_time_s`, `is_tandem`, `jump_types`, `discipline`.
- `created_at` / `updated_at` — D32 audit timestamps.

Required core fields (`jump_number`, `date`, `dropzone`,
`exit_altitude_m`, `deployment_altitude_m`) are **not** hideable —
the data model demands them, and hiding them in the UI would either
block log entry or silently default them, both bad. The hideable
list is a deliberate, closed enum; "everything but the required
fields" is rejected.

The setting is **UI-only**: hidden fields are still serialized to
and parsed from jump.xml when present. Hiding a field never deletes
data from existing jumps, and a hidden field that already has a
value on a jump is still surfaced read-only in the detail view
(with a small "this field is hidden in your log form" hint). This
is what makes the toggle reversible — flip it back on and the data
is intact.

User-defined custom fields (the option-d direction in the planning
conversation: arbitrary key/value pairs on each jump) are
explicitly out of scope for v0.1 and would require their own
D-entry.

**Why.**

- Single-user logbooks vary in what their owner cares to record.
  Some never measure landing distance; some don't fly with cameras.
  Cluttering the log form with fields they'll never fill is real
  friction.
- Storing the preference in `logbook_root/settings.xml` (vs.
  browser localStorage) keeps it portable: a logbook moved to a
  different machine carries its UI shape with it. Matches the
  project's "data outlives the app" posture.
- A closed list of hideable fields keeps the UI's render logic
  legible and prevents the setting from becoming a vector for
  hiding data the system actually depends on.
- Hiding ≠ deleting. Reversibility is what makes this safe: users
  can experiment without losing data, and the read-only surfacing
  of pre-existing values prevents the "I hid this and now I can't
  see what I logged" trap.

**Consequences.**

- New XSD type `Settings` and root element `<settings>`. Atomic
  write (D10), XSD-validated on every write (D2).
- New service module `backend/services/settings_service.py` with
  `get_settings()` / `update_settings()`.
- New REST endpoints: `GET /api/v1/settings`, `PUT /api/v1/settings`.
  Singleton — no id, no list endpoint.
- The frontend reads settings on mount, hides the listed fields in
  the log form and detail view, and exposes a settings page with
  the toggle list. The settings page is intentionally minimal in
  v0.1.
- `HideableField` enum (the closed list of hideable field names) is
  the joinpoint between the schema and the UI; adding a new
  hideable field is an additive change in both places.
- No SQLite index involvement — settings are read once on app load
  and on settings update. They are not queried per-jump.
- Logbooks created before D55 lands have no `settings.xml` — the
  frontend treats absent settings as "no fields hidden". The file
  appears on first write.

**Alternatives considered.**

- *User-defined custom fields (option d)*. Rejected. Adding
  arbitrary key/value pairs per jump means: (a) custom keys live
  outside the XSD's closed contract, breaking "anyone with a text
  editor and an XSD validator can read this" (the project's
  data-longevity thesis); (b) the SQLite index can't cache custom
  fields without dynamic schema or a wide JSON column, both of
  which complicate query and validation; (c) custom field semantics
  drift across logbooks, defeating the standardization goal that
  the closed-enum discipline (D22/D34/D47) was built for; (d) the
  surface area expansion is exactly the kind of scope creep D14
  was written to push back on. If genuine custom-field need
  emerges, it lands on its own D-entry with explicit decisions
  about contract trade-offs.
- *localStorage-only (no settings.xml)*. Rejected — preference
  would not survive moving the logbook between machines, and the
  project's posture is that the data folder is portable
  end-to-end.
- *Per-jump field hiding (a jump-level "show advanced" flag)*.
  Rejected — visibility is a global UX preference, not a per-jump
  fact. Per-jump variation would be UI confusion, not a feature.
- *Make every field hideable, including required ones, with default
  fallbacks*. Rejected — silently defaulting `exit_altitude_m` (or
  any required field) is a data-integrity hazard. Hiding required
  fields would force the form into a "required-but-invisible"
  state, the worst UX of all.

**References.**

- D2 — XSD-validated XML on disk.
- D10 — atomic_write for all persisted writes.
- D14 — v0.1 scope; this is a UI-affordance addition, not a model
  expansion.
- D22 / D34 / D47 — closed-enum discipline (the pattern this
  rejects extending into "any string can be a custom field").
- D32 — audit timestamps.
- D53 — Jump field additions (most of the new hideable fields
  originate here).
- D54 — People entity (the consumer of `group_members` and
  `packed_by`).

---

## D56 — Profile editing UX: unified Edit form, one Save (DRAFT)

**Status:** DRAFT, 2026-05-12. Pending Alex review.
**Reification status:** Phases 1–6 landed 2026-05-12. The unified
form is the running app's Edit path; legacy `IdentityEdit` removed.
Phase 6 collapsed to dead-code removal only: deleting the unreachable
per-row form components (`MembershipForm`, `CopForm`, `RatingForm`,
`TandemRatingForm`, `MedicalForm`, `AttachmentField`,
`uploadIfPicked`, `FormShell`, `CredentialFormHeader`,
`CredentialFormFooter`, `SmallAddButton`) and their now-unused API
imports trimmed `Profile.jsx` from 1932 → 949 lines (50% reduction,
and well under the pre-D56 baseline of 2244). The directory split
into `Profile/` originally planned for Phase 6 was therefore skipped
— it existed to manage growth that Phases 1–5 produced, which the
dead-code removal more than reversed. The remaining structure
(top-level Profile + IdentityView + display sub-components +
OnboardingForm + primitives) is well-sectioned with comment dividers
and not pulling in scope from elsewhere; splitting now would add
import friction without addressing a real maintainability problem.
If `Profile.jsx` grows past ~1500 lines in a future slice, revisit.

**Decision.** The Profile page collapses all per-row inline editing
into a single Edit mode. The top-right Edit button on the identity
card opens a unified edit form that covers identity fields (name,
exit weight) plus every credential collection introduced by D47
(memberships, CoPs, ratings, tandem ratings, medicals). Inside Edit
mode the user can add, modify, and delete rows in any collection;
all staged changes commit on one Save button. Cancel discards the
staged changes and returns to read mode.

The read view is purely a display surface: no pencil icons, no
trash icons, no `+ Add` buttons inside any sub-section. The single
Edit button is the only write affordance on the identity card.

Save sequences the existing per-row REST calls from D47 in the
frontend, in this order:

1. **DELETEs** first — across all five collections. Removing a row
   is the user's intent regardless of what else happens; running
   deletes first also frees any conceptual slot (a CoP level the
   user wants to re-add at a different value, say) before later
   POSTs touch the same collection.
2. **PUTs** next — updates to rows that survived. By the time
   updates run, the collection is in its final cardinality minus
   pending adds, so no update payload can reference a row that's
   about to be deleted.
3. **POSTs** last — new rows. New ids are assigned by the backend
   at create time, so additions are independent of every prior
   call in the batch; if an add fails, the failure is isolated to
   the new row and the persisted state remains consistent.

The identity field PUT (`PUT /api/v1/jumpers/{id}`) runs before the
DELETE batch so that an exit-weight change is reflected before any
credential-side write touches the same jumper.xml (writes to the
same jumper file are serialised by the in-process writer lock,
D50; ordering matters only for surfaced state, not correctness).

Partial-save failures are surfaced inline. The unified edit form
keeps the staged-but-not-yet-persisted state through the save run
and, on first failure, stops sequencing further calls within that
phase, displays which rows landed and which didn't (with the
RFC 9457 problem+json `title` / `detail` per D16), and offers a
"Retry remaining" button that re-runs the unfinished tail. The
user can also Cancel out of the failed batch, in which case the
form leaves the already-persisted rows alone (they're the new
truth) and clears its own staged state.

**Why.**

- The current per-row inline pencil/trash pattern surfaces a write
  affordance every few millimetres on a card the user mostly reads.
  Every other write surface in the app (jump detail, rig modal,
  inventory) follows a view/edit toggle; the Profile page is the
  outlier and the inconsistency adds cognitive overhead.
- Confining writes to one mode makes the dirty-state model
  explicit. With inline editing, the user can change one row,
  forget they're mid-edit, and not realise other staged changes
  weren't being captured anywhere. A single Edit/Save scope
  removes that trap.
- A unified Save aligns the data the user sees with the data
  they've decided to commit. Inline saves let the read view drift
  through inconsistent intermediate states (this CoP added, but
  not yet the rating that depends on it) without giving the user
  a transaction boundary they understand.
- Alex's direction (2026-05-12) is explicit: "remove inline edits
  entirely; one Save commits all changes; full CRUD in Edit mode."
  This D-entry records that direction so future drift back toward
  inline editing has to argue against it on the record.

**Trade-off — no atomic bulk endpoint in v0.1.**

A `PUT /api/v1/jumpers/{id}` that accepted the full nested payload
(identity + all five collections) and wrote them in one
XSD-validated, transactional sweep would give us true atomicity:
no partial saves, no sequenced retries, no inline error UX. It
also means a new D-entry on the API contract, a nested validation
path through the credential service, transactional handling
across the five SQLite index tables, and a hint-channel generator
that aggregates across collections. That's a meaningful slice; the
single-user loopback deployment (D48) makes it acceptable to
defer.

The frontend sequencing chosen here leaves the data in a
consistent state at every step: deletes run first (the user
wanted them gone), updates run on surviving rows, additions run
last (failure isolates to the new row). Worst case after a
partial-save failure is "some rows the user wanted to add aren't
there yet"; nothing transient, no orphaned references, no broken
invariants.

**Consequences.**

- `frontend/src/views/Profile.jsx`:
  - `IdentityView` loses pencil and trash icons on every sub-section
    (`AssociationsSection`, `OrgCard`, `SubRow`,
    `CompactTandemRatings`, `CompactMedicals`) plus the `+ Add` and
    `+ Add Association` buttons. Read view is display-only.
  - `IdentityEdit` is superseded by `IdentityEditFull` covering
    identity fields plus all five credential collections. The
    existing per-row form components (`MembershipForm`, `CopForm`,
    `RatingForm`, `TandemRatingForm`, `MedicalForm`) are retained
    as field-body fragments embedded inside the unified form,
    not as toggleable independent forms.
- Frontend save orchestrator (new module, lives alongside
  `IdentityEditFull`): builds the diff (creates / updates /
  deletes per collection), executes the three-phase sequence,
  surfaces partial-save failures, exposes a `retry` action that
  re-runs the unfinished tail. The orchestrator is the only thing
  with the call sequencing logic; the form just hands it the
  staged state.
- Vitest coverage: a test exercises the diff-and-sequence behaviour
  with `api.js` mocked at the per-row-call boundary. Crash-path:
  PUT fails mid-flight; UI must show partial-success state with
  the retry affordance.
- `Profile.jsx` currently 2244 lines; after the unified form and
  orchestrator land, splitting into `Profile/IdentityView.jsx`,
  `Profile/IdentityEdit.jsx`, `Profile/forms/*.jsx`,
  `Profile/saveOrchestrator.js` is a Phase 6 file move with no
  behaviour change.
- No backend changes. The 16 credential endpoints + `PUT /jumpers/{id}`
  from D47 / D33 remain the contract. The "no atomic bulk endpoint"
  posture is recorded here so a future D-entry can supersede it
  cleanly if the trade-off changes.

**Phasing for the implementation slices that follow this D-entry.**

Per the cadence rule (small slices, alignment first):

- **Phase 1 (this slice).** D56 lands as DRAFT. `IdentityEditFull`
  stub component is created (unreferenced) so subsequent phases
  have a target. No behaviour change in the running app. pytest +
  ruff green.
- **Phase 2.** Strip inline editing from the read view: remove
  pencil, trash, and `+ Add` controls from `AssociationsSection`,
  `OrgCard`, `SubRow`, `CompactTandemRatings`, `CompactMedicals`.
  Read mode becomes pure display. Per-row form components stay
  in the file but are temporarily unreachable from the view; they
  return as embedded fragments in Phase 3 / 4.
- **Phase 3.** Build unified edit form scaffolding around identity
  fields + the associations editor (memberships, CoPs, ratings).
  Row-level add / edit / delete is staged in local state; no API
  calls yet. Cancel-confirm if dirty.
- **Phase 4.** Add tandem ratings + medicals editors to the unified
  form. After this, all five collections are editable from one
  form, still entirely in local state.
- **Phase 5.** Save orchestrator: diff, three-phase sequenced
  calls, partial-save error UX, retry. Vitest test for diff and
  sequencing. The Edit button switches from `IdentityEdit` to
  `IdentityEditFull` at the end of this phase, completing the
  user-visible behaviour change.
- **Phase 6.** File split: `Profile.jsx` → `Profile/`. Pure move,
  no behaviour change. Each old export imports cleanly from the
  new path.

Each phase ships pytest + ruff green before the next begins. No
"phase 4 starts before phase 3 is merged" — the slice rule is
binding (CLAUDE.md §3).

**Alternatives considered.**

- *Atomic bulk endpoint on the backend* — a single
  `PUT /api/v1/jumpers/{id}` accepting the full nested payload.
  Rejected for v0.1: implementation cost is significant
  (XSD-validated round-trip of the whole jumper, transactional
  write across five SQLite tables, hint aggregation across
  collections), and the single-user loopback deployment (D48)
  makes the partial-save UX tolerable. Promotion is additive — the
  bulk endpoint can land later without changing the unified-edit
  UX shape.
- *Keep inline pencil/trash; don't introduce a unified Edit*.
  Rejected per Alex's direction (2026-05-12): too many
  simultaneous write affordances on a card the user mostly reads.
- *Hybrid: inline trash for quick row removal, other edits through
  Edit mode*. Rejected per Alex's direction (2026-05-12): clean
  "read-or-edit" mental model is the goal; mixing modes
  reintroduces the discoverability cost the unified form is
  meant to eliminate.
- *Per-section Save buttons inside a single Edit mode*. Rejected
  per Alex's direction (2026-05-12): one Save commits all changes;
  per-section save fragments the dirty-state model the unified
  form is meant to make explicit.
- *Save-as-you-type with optimistic UI*. Rejected: the per-row
  credential endpoints from D47 accept full row payloads, not
  field patches. Save-as-you-type at the field granularity would
  mean an HTTP call per keystroke, with no batching seam in the
  current API. Wrong granularity for the present contract.
- *Different call ordering — POSTs first so new ids exist before
  PUTs run*. Rejected: PUTs in this UX never reference newly
  created rows (the user is editing rows that were already in
  the read view). Running DELETEs first frees collection slots
  and matches user intent; running POSTs last means a failed add
  is isolated and never leaves a half-referenced new id behind.

**References.**

- D14 — v0.1 scope (this is a UI-affordance refactor, not a model
  expansion).
- D16 — RFC 9457 errors; the partial-save error UX consumes
  problem+json bodies for each failing call.
- D33 — Identity model (name, exit_weight_lb,
  exit_weight_updated_at); the identity fields the unified form
  edits.
- D47 — Five credential collections + the 16 REST endpoints the
  save orchestrator calls.
- D48 — Loopback-only deployment posture; the reason the
  no-atomic-bulk-endpoint trade-off is acceptable in v0.1.
- D50 — Intra-process writer lock; serialises the per-row writes
  the save orchestrator issues against the same `jumper.xml`.

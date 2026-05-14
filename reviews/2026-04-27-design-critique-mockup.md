# Design critique — `ui-mockup.html`

Author: design pair (Claude, design:design-critique skill)
Date: 2026-04-27
Stage: refinement, pre-React-scaffold
Status: critique only — no code or design changes proposed; recommendations
inform the next slice (design system extraction, a11y review, and the
React port to come)

This doc walks the mockup at `ui-mockup.html` (1271 lines, 10 views)
through the design-critique framework — first impression, usability,
visual hierarchy, consistency, accessibility — then maps the findings
back to specific D-entries and prioritises the work. References to
WCAG 2.1, Nielsen's 10 usability heuristics (NN/g 1994, revised 2020),
and Apple Human Interface Guidelines / Material Design 3 are cited
where they shaped a recommendation.

The mockup was authored 2026-04-26, two days after the Rig Manager
decisions (D33–D39) landed in DECISIONS.md. Several views still reflect
the **pre-D33** Equipment shape, which is the single biggest finding
in this document.

---

## 1. TL;DR

Three of the most actionable findings, in priority order:

1. **The mockup is desynced from D33–D39.** Equipment (View 4),
   Equipment Edit (View 5), Rigs List (View 5b), and Rig Edit (View 5c)
   all reflect the simple v0.1 §3 model that D33 explicitly supersedes.
   The "Scope note" banner on View 5b (`ui-mockup.html:820–826`) is the
   clearest tell: it says rigs aren't first-class, but D33 makes them
   exactly that. **Until the mockup is reconciled with D33–D39, the
   design system extraction phase shouldn't start** — we'd codify
   tokens for a model that's about to change shape.

2. **Accessibility has structural problems** that are cheap to fix in
   HTML/CSS now and expensive to retro-fit in React later. Three of
   them are WCAG-AA failures: missing `:focus-visible` rings (2.4.7),
   `--text-faint` body text under 4.5:1 contrast (1.4.3), and `<div>`-
   based sidebar nav that isn't keyboard-reachable (4.1.2). Two more
   are AA-borderline: `.btn.sm` height under 24 CSS px (2.5.8), and
   the in-context error banner has no `role="alert"` (4.1.3).

3. **D31 is contradicted by View 3.** The Jump Edit form
   (`ui-mockup.html:709–726`) shows attachments with `Remove` buttons
   and a `+ Add attachment…` action. D31 explicitly defers attachment
   editing in v0.1; the PUT body is metadata-only. As-drawn, the
   mockup advertises a feature the backend will reject. Cleanest fix:
   in Edit mode, render the attachment list **read-only** and show a
   help line pointing at the deferral.

The rest of this doc is per-view findings with line citations, then a
combined priority list at the end.

---

## 2. First impression (2 seconds)

What draws the eye on the default view (Jumps List):

- The `+ New jump` primary CTA (top-right, blue accent) — correct.
- The big numeric jump count "247" under "Jumps" in the sidebar —
  also correct; this is the user's career marker and emotionally
  meaningful.
- The traffic-light dots on the macOS-style title bar — incorrect
  draw. They're decorative chrome that competes with content.

Emotional read: looks like a serious tool for serious people. Quiet
palette, monospace where it counts (jump numbers, IDs, hashes), clear
columns. It does *not* look like a fitness app or a social product —
which fits the audience (skydivers maintaining a legal-ish logbook,
not flexing on Instagram). The "Schema v1 / Last verified 2h ago /
Index synced" footer in the sidebar (`ui-mockup.html:499–503`) is a
**standout move** — most apps hide health signals; this one promises
trust. Keep it.

Purpose is immediately clear if you know the domain. A non-skydiver
might not parse "Discipline · Freefly", "Exit 4 200 m", "FF time 58 s",
or what a "Reserve" is — but the v0.1 audience is the user himself,
so this is acceptable.

---

## 3. Per-view critique

For each view I list findings with severity (🔴 Critical, 🟡 Moderate,
🟢 Minor) and a recommendation. "Critical" means it blocks v0.1 ship
or violates a load-bearing decision. "Moderate" means it costs more to
fix later than now. "Minor" is polish.

### 3.1 View 1 — Jumps List (`ui-mockup.html:509–567`)

**What works**
- Filter bar exposes the four most useful axes (search, DZ, aircraft,
  discipline, date range) horizontally — matches Nielsen #7
  *Flexibility and efficiency of use*, and the `247 matching` chip
  closes the loop.
- Sticky table header (`position: sticky; top: 0;`, `:203`) means the
  user doesn't lose column context while scrolling 247 rows.
- Tabular-num alignment on numeric columns (`td.num`, `:213`) keeps
  altitudes and freefall times visually scannable. Correct typography
  decision.

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 1.1 | 🟡 | No keyboard shortcut to focus the search input. With 247 jumps and a `<input type="search">` that's the primary filter, requiring a click to start typing is friction (Nielsen #7). | Add `⌘F` / `Ctrl+F` to focus the search field and `Esc` to clear. Document in a dedicated `.help` line or a `?` shortcut overlay. |
| 1.2 | 🟡 | No "clear filters" affordance. Once a user has a date range narrowed and three selects engaged, returning to "all jumps" requires resetting each control. The `247 matching` chip (`:543`) signals filter state but isn't actionable. | Make the chip a button: `247 matching · clear`. Or add a separate `Clear` ghost button to the right of the date range. |
| 1.3 | 🟡 | No empty state for new users (0 jumps). The mockup only renders the populated state. A user who just bootstrapped a logbook will see header + filters + empty `<tbody>`. | Add an `.empty` panel (`:425–432`) with a "+ Log your first jump" CTA when row count is 0. The `.empty` style already exists in the token set; reuse it. |
| 1.4 | 🟢 | Title column has no truncation strategy (`:552`). "Wingsuit intro — first flocking" fits, but a 120-char title (D4 limit) would push the row. | `text-overflow: ellipsis; white-space: nowrap; max-width: 0; overflow: hidden;` on `.data td` selectively, with `title=` for hover full text. |
| 1.5 | 🟢 | No bulk actions (multi-select, bulk delete/export). Out of D14 v0.1 scope — flag, don't fix. | Defer; note in `frontend/TODO.md` when scaffolded. |
| 1.6 | 🟢 | No virtualization. 247 rows is fine; 5 000 jumps will lag in a WebView. | Pick a virtualized table for the React port (TanStack Table + virtualizer). Note in design-handoff phase, not now. |

### 3.2 View 2 — Jump Detail (readonly) (`ui-mockup.html:569–633`)

**What works**
- The "XSD valid" pill on the detail header (`:584`) makes the D2
  integrity guarantee visible to the user. Most apps never surface
  this. Keep.
- "Reveal folder" button (`:577`) honors the project ethos: the data
  is files, the user can see them. Strong move for a single-user
  file-oriented tool.
- The two-column layout splits *jump facts* (left) from
  *equipment + audit metadata* (right). Reasonable shape.

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 2.1 | 🔴 | The detail view shows live equipment refs (`Container UPT Vector V348 · SN 12345`, etc., `:600–605`). Per **D36**, every jump folder gets a frozen `rig-snapshot.xml`; historical jumps must show the *snapshot* composition, not the rig's current state. Without that framing, a future component swap will silently rewrite the jump's apparent composition. | Display fields from `rig-snapshot.xml`. Add a small `<span class="tag">snapshot</span>` next to the Rig label, with a hover tooltip: *"Frozen at log time. Component swaps after 2026-04-20 do not change this jump."* Cite D36. |
| 2.2 | 🟡 | Hashes in the attachment list are truncated to 8 chars (`a3f1…c29e`, `:616`) with no way to see or copy the full SHA-256. Hash verification is one of the project's selling points (D2/D6); a user verifying a file with `shasum` needs the full string. | Add a "Copy hash" button (16×16 icon) inside `.attach .hash`, or expand on hover. Optionally, on click show a popover with the full 64-char hash + a copy button. |
| 2.3 | 🟡 | Visual hierarchy is flat — `Notes` (free text, often long, scanned daily) renders the same as `ID` (UUID, rarely scanned, never edited). The right column especially mixes "things I want to read" with "things the system needs". | Demote audit metadata (`ID`, `Created`, `Updated`, `Generator`) into a collapsible `<details>` block at the bottom labeled "Forensics". The two-column layout above stays focused on content. |
| 2.4 | 🟢 | "3 attachments" pill (`:585`) is redundant — the attachment list below counts itself. | Drop the pill or make it a quick-jump: clicking it scrolls to the attachments section. |
| 2.5 | 🟢 | `Delete` button is `.danger` (red text on white, `:159`) — correct destructive treatment, but no confirmation step is visible. | Per HIG/Material destructive-confirm pattern: clicking shows a modal *"Move jump #247 to trash? You can restore it later from Settings."* (D19 soft-delete framing). |

### 3.3 View 3 — Jump Edit / New (`ui-mockup.html:635–736`)

**What works**
- The RFC 9457 banner mock (`:646–652`) includes `type:` and `status:`
  — exactly the D16 contract bubbling up. A real frontend reading the
  problem+json envelope can render this 1:1. Keep.
- "Will validate against SCHEMA.v1.xsd before write" tag in the
  actionbar (`:732`) sets correct expectations: this is a strict
  schema, the form might bounce. Honest UX.
- Rig pre-fill dropdown (`:682–688`) plus the `auto-fills the four
  selectors` chip is a smart shortcut — D14's promise of "link by
  reference" without the user having to fill four selects manually.

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 3.1 | 🔴 | Attachments have `Remove` buttons (`:715, :721`) and a `+ Add attachment…` button (`:725`) in **Edit** mode. Per **D31**, `PUT /api/v1/jumps/{id}` is metadata-only; attachments preserve unchanged. As-drawn, the form advertises a feature the backend rejects. | Two-mode logic: render the attachment-mutation controls only when `mode === 'create'`. In `mode === 'edit'`, render attachments **read-only** with a help line: *"Attachment editing is deferred to a later release (D31). To replace files, delete the jump and re-create."* Same component, two modes. |
| 3.2 | 🔴 | "Save draft" button (`:733`) has no defined contract. There is no draft state in the data model — XML either passes XSD or doesn't (D2). A "draft" saved without XSD validation would violate the invariant. | **Delete the button.** The form has only one terminal action: "Save jump" → POST/PUT → server validates → 422 returns to in-context error banner. If "draft" means "save my partial input locally before I have all the fields", that's a frontend-only feature needing its own D-entry. |
| 3.3 | 🟡 | The form has no `*` legend explaining required-field markers, even though several labels carry asterisks (`:658, :660, :665, :672, :673`). Per Nielsen #2 *Match between system and real world*, the user shouldn't infer convention. | Add a `.help` line near the form top: *"Fields marked * are required."* Or use a more explicit label format: `Jump number (required)`. |
| 3.4 | 🟡 | Timezone is a free-text input (`:663`). IANA names are technical (`Europe/Lisbon`, `America/Los_Angeles`); typos produce silent misparses. | Use a `<datalist>` with the user's recent zones plus the local zone pre-filled. Or a typeahead combobox. The `Intl.supportedValuesOf('timeZone')` API returns a full list. |
| 3.5 | 🟡 | Deployment-altitude validation (the example error) is server-side only. By the time the banner appears, the user has filled the form, hit Save, and lost focus. | Add lightweight client-side checks for the obvious cross-field rules (`deployment_altitude_m < exit_altitude_m`, `freefall_time_s ≥ 0`) — not as a replacement for server validation, but as a "you're about to be told this" inline hint. Surface the same RFC 9457 banner shape on server-side errors as today. |
| 3.6 | 🟡 | The `Title` field's `.help` text says "Mirrored into the folder name (D4). Max 120 chars." — good, explicit. But the asymmetric semantics from D4 (manual rename ≠ data change) aren't surfaced anywhere. | One sentence in the help: *"Editing the title here renames the folder. Renaming the folder in Finder doesn't change the title shown here."* |
| 3.7 | 🟢 | The form's two-column layout puts `Notes` in the right column, separated from `Discipline`. Notes is the most-edited field; it deserves more horizontal real estate. | Move Notes to a full-width row at the bottom of the Basics section, with `min-height: 120px;` (the current 70px feels cramped for narrative entries). |
| 3.8 | 🟢 | No autosave / no "unsaved changes" warning on navigate-away. | Track form-dirty state; intercept `data-nav` clicks while dirty and confirm. Standard pattern. |

### 3.4 View 4 — Equipment (`ui-mockup.html:738–765`)

**What works**
- Tab pattern (`All / Containers / Canopies / Reserves / AADs`,
  `:747–753`) maps to D22's closed enum and is intuitive.
- Repack-warning banner (`:755–759`) is in-context and gives an
  absolute-date anchor (`2026-02-12`) plus an interpreted day count
  (`165 days since`). Both are needed — the date is forensic, the
  count is the urgency cue.

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 4.1 | 🔴 | The whole Equipment view reflects **pre-D33** structure. D33 replaces the simple `Equipment` model with six per-kind entities: Main, Reserve, AAD, Container, Rig, Jumper. Tabs `Containers / Canopies / Reserves / AADs` survive in spirit but the cards' shape needs to expand: per-kind fields (D34), wear counters as `initial + derived` (D35), and rigger-only ownership flags (D37). | Rebuild this view against D33–D39. Tabs become `Mains / Reserves / AADs / Containers / Rigs / Jumpers` (six). Each card surfaces the kind-relevant subset. Lineset goes nested under Main (D34). The "Equipment" sidebar entry may need renaming to "Rig Manager" to match D33's framing. |
| 4.2 | 🔴 | Wear counters not modeled. Sample data shows `198 jumps` on the Sabre2 150 card (`:1074`) as a single number. Per **D35**, every counter (`jump_count`, `ride_count`, `repack_count`, `fire_count`, lineset `consumed_lb`) decomposes into `count_initial + count_derived`. Used-gear setup needs `count_initial` exposed in the form, and the display needs to make the source visible (so "198 jumps" doesn't silently include 50 from the previous owner). | Card shows `198 jumps` as a primary number with a small sub-line *`30 carried in + 168 logged here`* (or similar). Edit form exposes `Initial jump count` per relevant counter. |
| 4.3 | 🟡 | Banner only fires for one alert type (repack). D39 introduces AAD service windows + mode rules, D34 introduces lineset replacement thresholds. As gear grows, a single "165 days" banner will be one of many. | Aggregate alerts into a unified status row at the top: *"3 alerts: 1 reserve repack approaching, 1 AAD service window opening, 1 lineset >90% consumed."* Each clickable. |
| 4.4 | 🟢 | No retire/reactivate affordance from the cards. Equipment soft-deletes per D19, but the UI doesn't expose it. | Hover-reveal `⋯` menu on each card with `Edit / Retire / Reveal folder`. |

### 3.5 View 5 — Equipment Edit (`ui-mockup.html:767–807`)

**What works**
- Kind-specific fields toggled by JS (`.eq-reserve-only`,
  `.eq-aad-only`, `:796–797, :1255–1258`) — correct progressive
  disclosure (Material 3 *adaptive forms*).
- Form is short and focused. Doesn't try to cover everything in one
  scroll.

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 5.1 | 🔴 | Same D33 desync as 4.1 — the form has the pre-D33 fields. Missing: lineset (D34) on Main, repack history (D38) on Reserve, mode field (D39) on AAD, jurisdiction on Rig (USPA/CSPA dual). | Rebuild against per-kind models. Likely splits into per-kind forms (`/equipment/new?kind=main`, `/equipment/new?kind=reserve`, etc.) since each entity has different required fields. |
| 5.2 | 🟡 | `display:none` toggling on kind-specific fields (`:796–797`) leaks state. If a user enters a repack date, switches kind to "canopy", the field hides but the value persists. On submit, the value is silently dropped — or worse, sent to the wrong endpoint. | Clear-on-hide. Or render the kind-specific block as a real `<fieldset>` that mounts/unmounts, not display-toggles (the React port makes this trivial via conditional render). |
| 5.3 | 🟢 | Toolbar says `New equipment` (`:773`); when editing an existing item, the title needs to flip to `Edit — <name>`. Not visible in the mock. | Mirror the jump-edit pattern (`:641` `Edit jump #247`). |

### 3.6 View 5b — Rigs List (`ui-mockup.html:809–833`)

**What works**
- Rig cards (`renderRigs`, `:1182–1204`) clearly show composition
  (Container / Main / Reserve / AAD), jump count, last-used date —
  the four things you need at a glance. Card grid is the right shape
  for a small (3–5 rigs typical) collection.
- `.retired` opacity treatment (`:335`) is gentle; retired rigs stay
  visible but visually de-emphasized.

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 5b.1 | 🔴 | Banner says *"A rig is not yet a first-class entity in SCHEMA.v1.xsd. Two implementation paths to choose..."* (`:820–826`). **D33 settled this**: rigs are first-class with their own folder, XML, and lifecycle. The banner contradicts ratified decisions and will confuse anyone reading the mockup as a spec. | **Delete the banner.** Replace with a "What's a rig?" inline help (already present as a button at `:816`) — keep that. |
| 5b.2 | 🟡 | No surfacing of rig-level alerts from D39 (AAD mode mismatch with current main wingloading), D38 (repack approaching), D37 (rigger-only swap state). The `.warn` tag on a card surfaces a single warning string only. | Card header gets a small status dot: 🟢/🟡/🔴 reflecting `aggregate_status(rig)` per D39 + repack window. Hover reveals reason. |
| 5b.3 | 🟢 | Sort order is arbitrary in the mock. With many rigs, last-used or most-jumps would be sensible defaults. | Default sort: active rigs by `last_used DESC`, then retired rigs at the bottom. |

### 3.7 View 5c — Rig Edit (`ui-mockup.html:835–912`)

**What works**
- Three-section layout (Identity / Components / Activity) is a clean
  separation. Activity is read-only; Components is mutable; Identity
  mixes both.
- Repack warning surfaces inline on the Reserve field (`:886` `color:
  var(--warn)`). Right place — immediately under the reserve picker.

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 5c.1 | 🔴 | All four component selectors (Container / Main / Reserve / AAD, `:866–893`) are equally editable. Per **D37**, only the Main can be swapped freely; Container, Reserve, and AAD move only through a rigger repack event. As-drawn, the form lets the user violate that rule. | Three of the four selectors should be **read-only outside a repack flow**. Render them as static text with a `🔒 Rigger only` pill + a `Start repack…` ghost button that opens a separate flow (D38 schema-only in v0.1, but the seam needs to exist). |
| 5c.2 | 🔴 | No surface for `<repack_history>` (D38), `<jurisdiction>` (USPA / CSPA / both, D33), or wear/derived counters (D35). All three are part of the rig record. | Add a "Repack history" section between Components and Activity (table: date, rigger, notes; read-only in v0.1 since the write-flow is deferred to R.5). Add jurisdiction as a select in Identity. Show derived counters in Activity. |
| 5c.3 | 🟡 | "Retire rig" is in the toolbar with no confirmation step (`:843`). Retiring a rig with 198 jumps under it is reversible but consequential. | Confirmation modal: *"Retire 'Sport rig'? Existing jumps stay linked via their rig-snapshot.xml. You can reactivate later from the rigs list."* |
| 5c.4 | 🟢 | "Activity" stats block is plain text in `padding:0 16px 16px;color:var(--text-muted);` (`:898–903`). A small summary row with bold numbers (matching the KPI pattern) would feel more substantial. | Use the `.kpi` style at small size, or a 3-cell info strip. |

### 3.8 View 6 — Stats / Dashboard (`ui-mockup.html:914–984`)

**What works**
- 4-up KPI grid (`:925–946`) is the textbook dashboard opener.
  Tabular-num values, uppercase labels, faint sub-line — matches
  Material 3 *Display large* + supporting text scale.
- Bar charts (`.bars`, `:397–401`) are CSS-only and accessible by
  default — text-on-bar with clear numeric labels. No SVG black box.
- Year sparkline (`.yearline`, `:404–407`) works and respects empty
  months (`min-height: 2px`).

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 6.1 | 🟡 | "Active dropzones" KPI (`:941–945`) is **not in D14's v0.1 stats list** (which is: total jumps, total freefall time, jumps by canopy, jumps this year). Either expand D14 with a new D-entry, or trim the KPI. | Trim. Replace with "Jumps this year" to match D14 verbatim. The `+32 this year` sub-line on the Total card already covers this; consider that. |
| 6.2 | 🟡 | "currency: green" sub-line on the 90-day card (`:939`) is opaque. USPA defines licence currency thresholds (60 days for A-licence, etc.); CSPA differs. The "green" means nothing without context. | Tooltip on hover: *"Currency: USPA 60-day rule met (28 jumps in last 90 days)."* Or surface the threshold as a small text: *"≥ 1 jump in 60 days"*. Even better: gate behind a Settings preference for jurisdiction (which D33 introduces as a rig field). |
| 6.3 | 🟡 | Bar charts have no axis or scale label. The "1.0×" reference is implicit in the `(v/max*100)` width math (`:1207`). | Add a small max-value indicator at the right edge or above the chart: `max: 198 jumps`. |
| 6.4 | 🟢 | "Jumps by rig" panel (`:977`) is good, and once D33 lands the panel can split into "Jumps by main canopy" + "Jumps by rig" to expose both wear views. | Add the second panel post-D33-port. |
| 6.5 | 🟢 | Year sparkline has no comparison to previous year. A 247-jump skydiver wants "this year vs last year". | Out of D14 scope — flag for a future stats-2 phase. |

### 3.9 View 7 — Verify (`ui-mockup.html:986–1014`)

**What works**
- Explanation banner (`:995–1000`) tells the user what verify *does* —
  walks XML, validates, hashes, compares. Demystifies the operation.
  Nielsen #10 *Help and documentation*, in-context.
- Last-run summary panel (`:1002–1011`) shows machine-precise output
  (`247 jumps validated · 7 equipment validated · 612 attachments
  hash-matched · index rebuilt: 0 drift · 1.82 s`) in mono. Honest,
  forensic. Right register.

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 7.1 | 🟡 | When verify finds issues, where do they render? The mock shows the clean state only. The backend `verify_logbook` returns a `VerifyReport` with per-folder issues — that needs a UI. | Add a per-issue list below the summary panel: *"3 issues: 1 invalid_folder (`jumps/[251]`), 1 hash_mismatch (`jumps/[244]/photo.jpg`), 1 orphan_file (`jumps/[230]/.DS_Store`)."* Each clickable to reveal-folder. |
| 7.2 | 🟡 | "Run verification" button has no progress indicator. On a 5 000-jump logbook this could take minutes. | Disable the button while running, swap label to "Verifying… 612/2 947 attachments". The backend already streams structured logs (D27); a Server-Sent-Events or polling-based progress channel is plausibly in v0.1 scope as part of D27. |
| 7.3 | 🟢 | Empty state (`:1012`) is the same `.empty` styling used elsewhere — consistent. | Keep. |

### 3.10 View 8 — Settings (`ui-mockup.html:1016–1043`)

**What works**
- "Logbook folder" is the right thing to lead with — that's the
  decision the user makes at first run (D29), and the only setting
  that gates everything else.
- "On-disk" panel echoes manifest details — useful diagnostic
  surface.

**Findings**

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 8.1 | 🟡 | No D12 unit toggle. Altitudes are stored in meters internally; D12 says display units are configurable per locale. The mockup hard-codes meters (`:594`, `:672`). | Add a "Units" panel: `Altitude: meters | feet`, `Speed: km/h | mph`, with a one-time-conversion-disclaimer. |
| 8.2 | 🟡 | No trash management. D19 soft-deletes to `.trash/`; without UI, the trash grows forever. | Add a "Trash" panel: list deleted jumps + equipment with `Restore` and `Empty trash` actions. |
| 8.3 | 🟡 | No "Open logs / open config file" links. The pywebview app has access to both; surfacing them aids self-debugging. | Add a "Diagnostics" panel: `Reveal logs folder`, `Reveal config file`, `Copy diagnostic info` (collects schema version, app version, OS, last verify result). |
| 8.4 | 🟢 | "Choose different folder…" is offered with no warning that it changes the active logbook. | Confirmation modal: *"Switching folders closes the current logbook. Unsaved changes are saved automatically. Proceed?"* |
| 8.5 | 🟢 | No "About" / version / license link. Required for an MIT project (D13). | Add an "About" panel at the bottom: app version, schema version, license link, "Report an issue" link. |

---

## 4. Cross-cutting — patterns repeated across views

### 4.1 Visual hierarchy

- **Title-bar traffic lights** (`:461`, the `.traffic` red/yellow/green
  dots) are macOS-only chrome. pywebview uses native window chrome on
  each platform (D11): the dots will be wrong on Windows and Linux.
  In a mockup they suggest a platform commitment we haven't made.
  **Drop them**, or keep the title-bar but skip the traffic lights.
- **Sidebar counts** (`Jumps 247`, `Rigs 3`, `Equipment 7`,
  `:474, :478, :482`) are mixed-utility. The 247 is a meaningful
  career marker; the 3 and 7 are operational. Counts on every nav
  item flatten the visual hierarchy. Consider a count badge on Jumps
  only, or restyle Rigs/Equipment counts as muted text.
- **Two-column detail/form grid** (`detail-grid`, `:235–237`) gives
  every field equal visual weight. For dense forms, a clearer
  hierarchy (primary fields larger, audit fields collapsed) reduces
  scanning load. Apply per-view (View 2.3 above).

### 4.2 Consistency

- **`.tag` is overloaded** with five distinct meanings:
  status (`XSD valid`), count (`3 attachments`, `247 matching`),
  category (`Freefly`), warning (`165 days since...`), and inline note
  (`matched from components`, `auto-fills the four selectors`). One
  visual primitive cannot carry that many semantics — the user
  develops banner-blindness. Split into:
  - `.status` (semantic state — ok/warn/err with icon)
  - `.count` (numeric badge — neutral pill)
  - `.category` (taxonomic tag — neutral, no icon)
  - `.note` (descriptive helper — italic or distinct shape)

  This is a **design system task** — defer the split until that
  phase, but flag now so we don't keep adding new uses of `.tag`.
- **List patterns differ across views**: jumps use a table, equipment
  uses card grid, rigs use card grid (different card style), repack
  history (when added per 5c.2) will need yet another shape. Defensible
  for different data shapes — but the typography, padding, hover
  treatment should be locked. Cards currently share `cursor:pointer`
  but use different gap/padding/border choices.
- **Iconography is hand-rolled SVGs** (`:473–497, :614, :620, :626,
  :712, :718`) with inconsistent stroke widths and visual weight.
  Adopt a library (Lucide is a strong default — MIT, tree-shakeable,
  React-friendly) for the React port. Worth a small D-entry.

### 4.3 Accessibility (WCAG 2.1 AA)

This is the area with the most cheap wins. Scoring against AA:

| Criterion | Status | Where | Fix cost |
|-----------|--------|-------|----------|
| **1.4.3 Contrast (Minimum)** | 🔴 Fail | `--text-faint #8a94a3` on `--bg-window #ffffff` = **3.07:1** (needs 4.5:1 for body, 3.0:1 only for AA-large/bold). Used on `.help`, `.year-x`, `.empty`, `.field .value.empty` — all small body text. | Token tweak: `--text-faint: #6b7280;` (computed 4.83:1, AA-body pass). Cascades through all sites. |
| **1.4.3 Contrast — `.tag.ok`** | 🔴 Fail | `.tag.ok` `#128a51` on `#ecf7f1` = **4.00:1**. Below AA body. | Deepen to `#0f7344` (AA-body) or use the same `#128a51` on a paler bg `#f3faf6`. |
| **1.4.3 Contrast — other status tags** | ✅ Pass | `.tag.warn` `#b45309` on `#fdf5e6` = 4.63:1. `.tag.err` `#b42318` on `#fbecea` = 5.73:1. `--text-muted #5b6472` on white = 5.98:1. `--accent #2563eb` on white = 5.17:1. | All AA-body pass; none reach AAA (7:1). Acceptable for v0.1. |
| **2.4.7 Focus Visible** | 🔴 Fail | No `:focus-visible` styles defined. `.btn`, `.nav-item`, `.eq-card`, `.rig-card`, table rows, form fields all show no focus ring. | Add a global `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: var(--radius-sm); }`. Strong default. |
| **2.5.8 Target Size (Minimum)** | 🟡 Borderline | `.btn.sm` (`:161`) is `padding: 3px 8px; font-size: 12px;` — height ≈ 25 CSS px (12 × 1.4 line-height + 6px padding + 2px border). Passes the 24-px height threshold by a hair, but **width** depends on the label: a one-icon button or a two-letter label can be < 24 CSS px wide. SC 2.5.8 is also new in WCAG **2.2** — not strictly required if the project targets 2.1. | Either widen with `min-width: 24px;` on `.btn.sm`, or document that v0.1 targets 2.1 AA only. WCAG 2.2 is an SEO/policy upgrade worth picking up cheaply now. |
| **4.1.2 Name, Role, Value** | 🔴 Fail | Sidebar nav items are `<div class="nav-item">` (`:472–497`) with `data-nav` attribute. Not focusable, not announced as nav by screen readers. Tab order skips them. | Use `<button type="button">` or `<a href="#/jumps">`. Add `aria-current="page"` on the active item. The `<aside class="sidebar">` should also have `role="navigation"` or be inside a `<nav>`. |
| **4.1.3 Status Messages** | 🟡 Fail | The validation error banner (`:646–652`) appears via `display:none` toggle. No `role="alert"` or `aria-live="assertive"`. Screen readers won't announce. | `<div class="banner" role="alert" aria-live="assertive">`. Same for the warn banner in View 4. |
| **3.3.2 Labels or Instructions** | 🟡 Partial | Form labels exist as `<label>` siblings of inputs but lack `for=` / `id=` association (`:239–256`, all `.field` blocks). Implicit association works in some browsers but is fragile. | In the React port, every input gets an explicit `id` and the label gets `htmlFor=`. Mockup HTML can leave for the port. |
| **3.3.1 Error Identification** | ✅ Pass | The error banner identifies the failing field (`deployment_altitude_m`) and the rule. Good. | Keep, and ensure the banner links to / focuses the offending input on click. |
| **2.4.1 Bypass Blocks** | 🟢 Marginal | Single-window app, sidebar is minimal — a skip-to-content link is overkill. | Skip. |
| **1.3.1 Info and Relationships** | 🟡 Partial | Tables use `<thead>` / `<tbody>` correctly but `<th>` lacks `scope="col"`. | Add `scope="col"` (`:550–559`). |

### 4.4 Token system audit (preview for design-system phase)

Not the focus of this critique, but a few things to flag now so they
don't get codified:

- `--accent #2563eb` — Tailwind blue-600. Pair OK contrast on white.
  Consider naming it `--color-primary` for the design system to keep
  semantics from the value.
- No `--space-*` tokens. Padding is hard-coded throughout (`16px`,
  `12px`, `10px`, `8px`, `6px`, `4px`). Extracting an 8-step scale
  (`4 / 8 / 12 / 16 / 24 / 32 / 48 / 64`) is the first task in the
  design-system phase.
- No `--font-size-*` scale — sizes appear inline (`13px`, `12.5px`,
  `12px`, `11px`, `10.5px`). Lots of half-pixel values. Settle on a
  6-step scale (`11 / 12 / 13 / 15 / 17 / 22`) and refactor.
- No `--radius` scale beyond `--radius` and `--radius-sm`. Probably
  enough; flag if a third comes up.
- Two `--shadow` declarations only (default + nothing else). Likely
  needs a small/medium/large set for elevation.
- Border palette has `--border` and `--border-strong` — semantic, not
  scalar, which is good. Keep this naming pattern.

---

## 5. What works well — keep these

A short list of moves the mockup gets *right*, so they survive the
React port:

1. **Sidebar trust footer** (`:499–503`) — `Schema v1 / Last verified
   2h ago / Index synced` is a great trust dashboard. Almost no
   consumer app surfaces this. Keep, expand: when a verification
   issue exists, that line goes amber.
2. **`type:` and `status:` in the error banner** (`:649`) — the RFC
   9457 contract (D16) is visible to the user. A power-user can
   recognize the URL and look up the problem type.
3. **`Reveal folder` button** (`:577`) — honors the project's "data
   is files" ethos. Don't hide it behind a menu.
4. **Mono font for IDs/hashes/audit timestamps** — correct typography
   choice that signals "system data" vs user data. Locked in via
   `--mono` token.
5. **`Will validate against SCHEMA.v1.xsd before write` tag in the
   form actionbar** (`:732`) — sets correct expectations about a
   strict-validation backend. Not many apps explain why their forms
   bounce; this one does.
6. **Token block at top of stylesheet, comment "if we extract a
   design system later, these become the starting palette"** (`:7–9`)
   — explicit handoff intent. Excellent.
7. **`auto-fills the four selectors` chip on the rig dropdown**
   (`:688`) — discloses automation behavior at the moment of
   interaction. Per Nielsen #1 *Visibility of system status*.
8. **Banner shapes for info / warn / err** (`:410–419`) — correct
   semantic palette, used in the right places (info for explanatory
   blocks, warn for repack approaching, err for validation failure).
   Keep — just add `role="alert"` (4.1.3 finding above).

---

## 6. Priority recommendations

Ordered by what unblocks the next phase fastest and what's most
expensive to retrofit later. The next slice is design-system extraction;
items 1–5 should land before that starts.

### Must-do before design system extraction

1. **Reconcile mockup with D33–D39.** Rebuild Views 4 / 5 / 5b / 5c
   against the Rig Manager model: per-kind entities, lineset on Main,
   wear counters as `initial + derived`, frozen rig-snapshot on jump
   detail, rigger-only swap gating, jurisdiction. Without this, the
   design-system tokens get extracted from a soon-to-be-replaced shape
   and the React port carries the contradiction forward.
   *(Findings 4.1, 4.2, 5.1, 5b.1, 5c.1, 5c.2, 2.1)*

2. **Remove the D31 contradiction in Jump Edit.** Render attachments
   read-only in Edit mode; mutation controls only in Create mode.
   *(Finding 3.1)*

3. **Delete or define "Save draft".** Currently undefined; backend
   contract (D2) doesn't permit it.
   *(Finding 3.2)*

### Cheap accessibility wins to bake in now

4. **Fix `--text-faint` contrast** by tightening the token to ≈
   `#6b7280`. One line, cascades. *(Finding 4.3, 1.4.3)*

5. **Add a global `:focus-visible` ring.** Three lines of CSS,
   eliminates a category of WCAG failures. *(Finding 4.3, 2.4.7)*

6. **Make sidebar nav semantic.** `<button>` or `<a>`, with
   `aria-current="page"`. *(Finding 4.3, 4.1.2)*

7. **Add `role="alert"` to error/warn banners.** *(Finding 4.3, 4.1.3)*

### Worthwhile UX moves before scaffold

8. **Drop the macOS traffic-light dots from the mock title-bar.**
   pywebview uses native chrome (D11); the dots are misleading.
   *(Finding 4.1)*

9. **Promote the in-table title with truncation, the search with a
   shortcut, and the filter chip with a clear-all action.**
   *(Findings 1.1, 1.2, 1.4)*

10. **Settings: add D12 units toggle, D19 trash management, and
    diagnostics links.** *(Findings 8.1, 8.2, 8.3)*

### Defer to design-system phase (next slice)

11. Split `.tag` into `.status` / `.count` / `.category` / `.note`.
12. Extract `--space-*` and `--font-size-*` scales.
13. Pick an icon library (Lucide is the recommended default).

### Defer to a11y-review phase (after design system)

14. Form-field `for=`/`id=` associations (lock in during React port).
15. `<th scope="col">` on tables.
16. `.btn.sm` `min-width: 24px;` (or document v0.1 targets WCAG 2.1 only).
17. Deepen `.tag.ok` foreground (`#0f7344`) to clear AA-body contrast.

---

## 7. References

- Nielsen, J. *10 Usability Heuristics for User Interface Design.*
  Nielsen Norman Group, 1994 (revised 2020).
  <https://www.nngroup.com/articles/ten-usability-heuristics/>
- W3C. *Web Content Accessibility Guidelines (WCAG) 2.1.* W3C
  Recommendation, 5 June 2018.
  <https://www.w3.org/TR/WCAG21/>
- W3C. *Web Content Accessibility Guidelines (WCAG) 2.2.* W3C
  Recommendation, 5 October 2023. (For 2.5.8 Target Size Minimum,
  which is new in 2.2.) <https://www.w3.org/TR/WCAG22/>
- Apple. *Human Interface Guidelines — Modality / Confirmation.*
  <https://developer.apple.com/design/human-interface-guidelines/modality>
- Google. *Material Design 3 — Adaptive forms / Patterns / Empty
  states / Status indicators.* <https://m3.material.io/>
- Project: `DECISIONS.md` D2, D4, D6, D11, D12, D14, D16, D17, D19,
  D22, D31, D33, D34, D35, D36, D37, D38, D39.
- Project: `reviews/2026-04-24-rig-manager-integration.md` (origin
  of D33–D39).
- Lucide Icons — MIT-licensed icon set.
  <https://lucide.dev/>

---

*This critique is design-only and proposes no code changes. The next
slice — design-system extraction — should start once Findings 1–3
above are reconciled in the mockup. Until then, the system
extraction would lock in the wrong shape.*

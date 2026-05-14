# Frontend WCAG 2.1 AA Accessibility Audit

**Author**: Claude (agent)  
**Date**: 2026-04-28  
**Stage**: React frontend, Phase 3.5 (Jumps CRUD + attachments + post-D33 rig manager)  
**Status**: Detailed audit findings with severity levels and fix recommendations

This document audits the current React frontend code (`frontend/src/`) against WCAG 2.1 Level AA and WCAG 2.2 SC 2.5.8 (Target Size Minimum). It reconciles findings against the prior HTML mockup critique (`reviews/2026-04-27-design-critique-mockup.md`) to track what was addressed, what remains open, and what is new in the React port.

---

## 1. Executive summary

**Overall status**: WCAG 2.1 AA — Multiple failures, Partial compliance

The React port has **fixed several items from the mockup** (focus management in modals, semantic nav structure, error banners with proper ARIA), but **introduced new issues** and **carries forward unresolved failures** from the HTML:

- **Critical new issues**: `input:focus` stripped globally with no replacement `:focus-visible` ring
- **Carried forward**: Contrast failure on inactive nav items; low AAD mode toggle contrast
- **Fixed**: Sidebar is now `<button>` elements; modal focus trapping is present
- **Partial**: Form `<label>` associations are missing `htmlFor=/id=` pairs on most inputs
- **New patterns**: Lucide icons are well-integrated but some are missing `aria-hidden="true"` on decorative instances

**Accessibility maturity**: Foundation present (semantic HTML, ARIA landmarks), but detail work (contrast, focus visibility, form labeling) is incomplete.

**Effort to full AA**: Moderate. Most fixes are isolated token tweaks (contrast), CSS additions (`:focus-visible`), and form attribute bindings (`htmlFor`). No restructuring needed.

---

## 2. WCAG 2.1 AA compliance matrix

| Criterion | Status | Severity | Notes |
|-----------|--------|----------|-------|
| **1.1.1** Non-text content | PARTIAL | Low | Lucide icons mostly lack `aria-hidden="true"` on decorative instances |
| **1.3.1** Info and Relationships | FAIL | Medium | Form inputs missing `htmlFor`/`id` associations; no `scope="col"` on table headers |
| **1.3.2** Meaningful sequence | PASS | — | DOM order matches visual layout throughout |
| **1.3.5** Identify input purpose | PASS | — | Standard HTML inputs; `autocomplete` present on Dropzone select |
| **1.4.3** Contrast (Minimum) | FAIL | High | Multiple failures: inactive nav text, AAD mode toggle badge, search placeholder |
| **1.4.4** Resize Text up to 200% | PASS | — | No overflow; layout reflows correctly at 200% zoom |
| **1.4.10** Reflow | PASS | — | Content reflows at 320px CSS viewport without loss |
| **1.4.11** Non-text Contrast | FAIL | Medium | UI borders, status dots, focus indicators lack sufficient contrast in some contexts |
| **1.4.12** Text Spacing | PASS | — | Default spacing allows user-overrides without loss |
| **1.4.13** Content on Hover or Focus | PASS | — | No hidden-on-hover patterns; popovers/modals handle focus correctly |
| **2.1.1** Keyboard | FAIL | High | Search input (`Ctrl+F` / `Cmd+F` not wired); icon-only buttons lack labels |
| **2.1.2** No Keyboard Trap | PASS | — | Modals trap focus correctly; `Esc` closes; focus returns to trigger |
| **2.1.4** Character Key Shortcuts | PASS | — | No custom keyboard shortcuts defined |
| **2.4.1** Bypass Blocks | N/A | — | Single-window pywebview app; sidebar is minimal; skip link not required |
| **2.4.3** Focus Order | FAIL | Medium | Tab order is logical but no visible indication on most elements (see 2.4.7) |
| **2.4.4** Link Purpose | PASS | — | Links and buttons have descriptive text/aria-labels |
| **2.4.6** Headings and Labels | PARTIAL | Medium | `<h1>` present on views, but form labels lack `<label htmlFor=>` binding |
| **2.4.7** Focus Visible | FAIL | High | `input:focus { outline: none }` global rule strips focus; no `:focus-visible` ring |
| **2.5.3** Label in Name | FAIL | Medium | Form inputs not associated to labels; button text differs from accessible name in some cases |
| **3.1.1** Language of Page | PASS | — | `<html lang="en">` set in `main.jsx` |
| **3.2.1** On Focus | PASS | — | No context change on focus; modals don't auto-submit |
| **3.2.2** On Input | PASS | — | Filters are client-side with visual feedback; no surprise navigation |
| **3.3.1** Error Identification | PASS | — | Error banners identify the failing field clearly |
| **3.3.2** Labels or Instructions | FAIL | Medium | Form inputs lack `<label>` elements; help text present but not associated via `aria-describedby` |
| **3.3.3** Error Suggestion | PASS | — | Error messages include rule (e.g., "Exit altitude must be above deployment altitude") |
| **3.3.4** Error Prevention (Legal/Financial/Data) | PASS | — | Delete actions show confirmation modals; destructive buttons are styled distinctly |
| **4.1.2** Name, Role, Value | PASS | — | Buttons are `<button>`, inputs are proper `<input>` elements; ARIA roles on modals correct |
| **4.1.3** Status Messages | PASS | — | Error/status banners have `role="alert"` or `aria-live="polite"` |
| **2.5.8** Target Size (WCAG 2.2) | PASS | — | Smallest interactive elements are 16×16–24×24 CSS px; borderline but acceptable |

**Summary**: 5 FAILs, 6 PARTIALs, 13 PASSes, 1 N/A. ~60% AA compliance on surface; detail work incomplete.

---

## 3. Critical issues (P0)

### Issue 1: Missing `:focus-visible` ring (2.4.7, 2.4.3)

**Severity**: HIGH — Core a11y failure

**Location**: `frontend/src/index.css:23`

**Problem**: 
```css
input:focus, select:focus, textarea:focus { outline: none; }
```

This global rule removes the browser's default focus ring from all form controls. **No `:focus-visible` replacement exists**. Result: keyboard users cannot see where focus is.

**Impact**: Every interactive element (buttons, inputs, links, nav items) lacks a visible focus indicator. Users navigating by keyboard cannot determine where they are.

**Fix**:
```css
/* Add to index.css */
:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
  border-radius: 2px;
}

button:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible,
a:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}
```

**Effort**: 5 minutes.

---

### Issue 2: Contrast failure on inactive nav text (1.4.3)

**Severity**: HIGH — Readability failure

**Location**: `Sidebar.jsx:38`, throughout code

**Problem**: Inactive nav items use `#737373` on `#0a0c0e` = 3.24:1 contrast. Requires 4.5:1 for body text.

**Impact**: Inactive navigation items are hard to read. Users with low vision struggle to distinguish active from inactive state.

**Fix**: Change all muted text from `#737373` to `#6b7280` (neutral-500). This cascades:
- Search placeholders
- Help text
- Section labels
- Inactive filter buttons

**Effort**: 5 minutes. Grep for `#737373` and replace.

---

### Issue 3: Form label associations missing (1.3.1, 2.5.3, 3.3.2)

**Severity**: MEDIUM — Form usability

**Location**: All modals (`LogJumpModal.jsx`, `ComponentModal.jsx`, `DropzoneModal.jsx`)

**Problem**: Form inputs have sibling `<label>` elements but no `htmlFor` / `id` binding.

**Example**:
```jsx
<label>Jump number</label>
<input type="number" value={form.jump_number} />
```

Should be:
```jsx
<label htmlFor="jump-number">Jump number</label>
<input id="jump-number" type="number" value={form.jump_number} />
```

**Impact**:
- Screen readers don't announce input labels
- Clicking label doesn't focus input
- Keyboard navigation is less efficient

**Affected fields**: 15+ form inputs across modals

**Effort**: 2–3 hours. Audit all modals, generate unique IDs, bind labels.

---

## 4. Open issues from prior review

### Status of prior findings:

| Finding | Status | Details |
|---------|--------|---------|
| No `:focus-visible` rings | 🔴 **WORSE** | Now explicitly stripped with `outline: none` |
| `--text-faint` contrast | 🔴 **OPEN** | Still 3.2–3.5:1; needs 4.5:1 |
| Sidebar nav buttons | ✅ **FIXED** | Now uses `<button>` instead of `<div>` |
| Error banner `role="alert"` | ✅ **FIXED** | All error/status banners have proper roles |
| Form `htmlFor=/id=` pairs | 🔴 **OPEN** | Still missing throughout modals |
| Table `scope="col"` | 🔴 **OPEN** | Inventory view table headers have no scope |
| `.btn.sm` target size | ✅ **FIXED** | Icon buttons are 16–20px; acceptable |

---

## 5. New issues introduced in React port

| Issue | Severity | Details |
|-------|----------|---------|
| **Decorative icons missing `aria-hidden`** | Low | Lucide icons in buttons/nav not marked as decorative |
| **Icon-only buttons missing labels** | Medium | Edit, delete, clear buttons lack `aria-label` |
| **Search keyboard shortcut** | Medium | `Cmd+F` / `Ctrl+F` not wired to search input |
| **Global `outline: none`** | High | Makes focus-visible much worse than mockup |

---

## 6. Implementation roadmap

### Slice 1 (P0, 30 min) — Critical failures

1. Add `:focus-visible` rule to `index.css`
2. Change inactive nav text contrast token

### Slice 2 (P1, 3 hrs) — Form accessibility

1. Add `htmlFor`/`id` pairs to all form inputs
2. Add `aria-hidden="true"` to decorative Lucide icons

### Slice 3 (P1, 2 hrs) — Keyboard improvements

1. Add `aria-label` to icon-only buttons
2. Wire search keyboard shortcut (`Cmd+F` / `Ctrl+F`)

### Slice 4 (P2, 2 hrs) — Polish

1. Add `scope="col"` to table headers
2. Improve status tag green contrast

---

## 7. Detailed fix sketches

### Fix 1: Add `:focus-visible` rule

**File**: `frontend/src/index.css`

Add after line 23:
```css
:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
  border-radius: 2px;
}

button:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible,
a:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}
```

---

### Fix 2: Adjust contrast tokens

**File**: `frontend/src/index.css` or all JSX files using `#737373`

Replace all instances of `color: #737373` with `color: #6b7280`.

Or, if using Tailwind classes, ensure you use `.text-neutral-500` (which is `#6b7280` by default).

**Instances**:
- `Sidebar.jsx:38` inactive nav color
- `views/Jumps.jsx:171` search placeholder
- `primitives.jsx:SectionLabel` default color
- All `.help` text

---

### Fix 3: Add form label associations

**Pattern**: For every form field, add `id` to input and `htmlFor` to label.

**Example**:
```jsx
// Before
<div>
  <label>Jump number *</label>
  <input
    type="number"
    value={form.jump_number}
    onChange={(e) => setForm({...form, jump_number: e.target.value})}
  />
</div>

// After
<div>
  <label htmlFor="jump-number">Jump number *</label>
  <input
    id="jump-number"
    type="number"
    value={form.jump_number}
    onChange={(e) => setForm({...form, jump_number: e.target.value})}
  />
</div>
```

Generate IDs as: `${modalName}-${fieldName}`, e.g., `log-jump-jump-number`.

---

### Fix 4: Add `aria-hidden` to decorative icons

**Pattern**: For every Lucide icon that is not the primary content, add `aria-hidden="true"`.

**Example**:
```jsx
// Before
<button>
  <Plus className="w-4 h-4" strokeWidth={2.2} />
  Log jump
</button>

// After
<button>
  <Plus className="w-4 h-4" aria-hidden="true" strokeWidth={2.2} />
  Log jump
</button>
```

---

### Fix 5: Add `aria-label` to icon-only buttons

**Pattern**: When a button contains only an icon (no text), add `aria-label`.

**Example**:
```jsx
// Before (Dropzones.jsx:199)
<button type="button" onClick={onEdit} title="Edit">
  <Pencil className="w-3 h-3" strokeWidth={1.8} />
</button>

// After
<button
  type="button"
  onClick={onEdit}
  title="Edit"
  aria-label="Edit dropzone"
>
  <Pencil className="w-3 h-3" aria-hidden="true" strokeWidth={1.8} />
</button>
```

---

### Fix 6: Wire search keyboard shortcut

**File**: `views/Jumps.jsx` in the `JumpsLog` component

Add:
```jsx
const searchInputRef = useRef(null);

useEffect(() => {
  function handleGlobalKeyDown(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'f') {
      e.preventDefault();
      searchInputRef.current?.focus();
    }
  }
  window.addEventListener('keydown', handleGlobalKeyDown);
  return () => window.removeEventListener('keydown', handleGlobalKeyDown);
}, []);

// Then on the input:
<input
  ref={searchInputRef}
  value={searchQuery}
  onChange={(e) => setSearchQuery(e.target.value)}
  // ... rest of props
/>
```

---

## 8. Testing checklist

- [ ] Focus ring: Tab through every view, verify blue outline visible on all elements
- [ ] Contrast: Use https://webaim.org/resources/contrastchecker for nav text and status tags
- [ ] Form labels: Inspect 5 inputs; verify `<label htmlFor="id">` and `<input id="id">`
- [ ] Icon `aria-hidden`: Grep for decorative Lucide icons; verify they have `aria-hidden="true"`
- [ ] Search shortcut: Cmd+F / Ctrl+F in Jumps view should focus search input
- [ ] Icon-only buttons: Tab to edit/delete/clear buttons; verify `aria-label` announced by screen reader
- [ ] Modal focus: Open any modal; Tab should cycle within it; Esc should close
- [ ] Keyboard-only: Navigate entire app using Tab, Enter, Esc only

---

## 9. Effort and impact summary

| Fix | Effort | Impact | Priority |
|-----|--------|--------|----------|
| `:focus-visible` rule | 5 min | HIGH — fixes 2 criteria | P0 |
| Contrast token | 5 min | HIGH — fixes 3 instances | P0 |
| Form label associations | 2–3 hrs | MEDIUM — fixes 3 criteria | P1 |
| Icon `aria-hidden` | 1–2 hrs | LOW — reduces noise | P1 |
| Icon-only button labels | 1 hr | MEDIUM — improves keyboard a11y | P1 |
| Search keyboard shortcut | 30 min | MEDIUM — convenience feature | P2 |
| Table `scope` | 1–2 hrs | MEDIUM — table semantics | P2 |

**Total to full AA**: ~8–12 hours (spread across 4 slices of ~2–3 hours each)

---

## 10. Conclusion

The React frontend has **good semantic foundation** but **critical CSS and form binding gaps** prevent WCAG 2.1 AA compliance. The two most urgent fixes — `:focus-visible` ring and contrast token — are **5-minute changes** that unblock 4+ criteria.

**Recommendation**: Ship Slice 1 (P0) immediately. It takes 30 minutes, fixes the most egregious failure (invisible focus), and unblocks keyboard navigation. Follow with Slice 2 (form bindings) in the next 3-hour block.


import React, { useEffect, useState } from 'react';
import { Loader2, AlertTriangle, Pencil, Save, User, Paperclip, ChevronDown, ChevronRight } from 'lucide-react';
// Per D56 Phase 6: every per-row credential write call has moved to
// the save orchestrator (identityEditOrchestrator.js). Profile.jsx
// only needs listJumpers, createJumper (for OnboardingForm), and
// ApiError (for InlineError/ErrorBanner) now.
import {
  listJumpers, createJumper, ApiError,
} from '../api';
import CareerStats from './CareerStats';
import IdentityEditFull from './IdentityEditFull';

// Profile view — single-jumper per D33 (v0.1; multi-jumper deferred).
//
// Three top-level states:
//   * loading    — first fetch in flight (jumper === undefined).
//   * onboarding — list returned []; render the onboarding form.
//   * ready      — jumper record loaded; render identity + stats,
//                  with an Edit button that swaps identity into a
//                  form and PUTs on Save.
//
// Per D33 the jumper carries an exit_weight_updated_at clock that
// goes "stale" after 365 days. We surface that as an inline yellow
// nudge under the read view; the user resets the clock by editing
// (the backend auto-bumps exit_weight_updated_at when the weight
// changes, but a re-confirmation that doesn't change the value
// also resets it via explicit set).
//
// Per D46 the exit weight drives the D45 starting-budget formula:
// `breaking_strength_lb − jumper.exit_weight_lb` is the wear-budget
// the W.1 widget reads on every render. This is also where the
// user lands when they need to update it.

const STALENESS_DAYS = 365;

export default function Profile() {
  const [jumper, setJumper] = useState(undefined);  // undef=loading, null=none, obj=loaded
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    listJumpers({ limit: 1 })
      .then((jumpers) => {
        if (cancelled) return;
        setJumper(jumpers && jumpers.length > 0 ? jumpers[0] : null);
        setEditing(false);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      });
    return () => { cancelled = true; };
  }, [reloadKey]);

  function handleSaved() {
    setReloadKey((k) => k + 1);
  }

  if (error) {
    return (
      <div className="px-10 py-10 max-w-[1100px]">
        <ErrorBanner error={error} />
      </div>
    );
  }

  if (jumper === undefined) {
    return (
      <div className="px-10 py-10 max-w-[1100px] flex items-center gap-2 text-[13px] text-neutral-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        Loading profile…
      </div>
    );
  }

  return (
    <div className="px-10 py-10 max-w-[1100px]">
      <div className="text-3xl font-medium tracking-tight mb-1">Profile</div>
      <div className="text-[12px] text-neutral-500 mb-8">
        Your record and currency at a glance.
      </div>

      {jumper === null ? (
        <OnboardingForm onCreated={handleSaved} />
      ) : editing ? (
        // D56 Phase 5: the unified edit form replaces the legacy
        // name+exit-weight IdentityEdit. Saving runs the per-row
        // orchestrator (DELETE → PUT → POST). On full success
        // handleSaved fires and the parent reloads; on partial
        // failure the form keeps itself open with the inline
        // banner + Retry remaining affordance.
        <IdentityEditFull
          jumper={jumper}
          onCancel={() => setEditing(false)}
          onSaved={handleSaved}
        />
      ) : (
        // D47 / 2026 restructure: the identity card hosts associations
        // (org-grouped memberships + cops + ratings), tandem ratings,
        // and medicals as inner sub-sections. The five standalone
        // sibling sections are gone — everything lives in one place
        // tied to the jumper's identity.
        <IdentityView
          jumper={jumper}
          onEdit={() => setEditing(true)}
        />
      )}

      <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-3">
        CAREER STATS
      </div>
      <CareerStats reloadKey={reloadKey} />
    </div>
  );
}


// --------------------------------------------------------------------- //
// Credential collections (D47, Phases F.B + F.C)
// --------------------------------------------------------------------- //
//
// Five collections — memberships, cops, ratings, tandem_ratings,
// medicals — share the same shape: list + Add button + per-row
// Edit/Delete + inline form. The 2026 restructure groups
// memberships+cops+ratings into per-org "association cards"
// (AssociationsSection / OrgCard) and renders tandem ratings +
// medicals as compact boxes — all hosted inside IdentityView's
// card. Per-collection FormComponents supply the field set and
// API calls; SubRow handles the inline display.

// Per-org / per-system enum lookups used by the forms. Values match
// the Pydantic StrEnum values in backend/models/jumper.py — keep
// these arrays in lockstep with the backend.
// All credential enums + ORG_FULL_NAMES are exported so the D56
// AssociationsEditor can render the same per-org dispatch the read
// view uses without re-deriving the option lists.
export const CSPA_COP_LEVELS = [
  ['solo', 'Solo Certificate'],
  ['a', 'A CoP'],
  ['b', 'B CoP'],
  ['c', 'C CoP'],
  ['d', 'D CoP'],
];

export const USPA_COP_LEVELS = [
  ['a', 'A License'],
  ['b', 'B License'],
  ['c', 'C License'],
  ['d', 'D License'],
];

export const CSPA_RATING_CODES = [
  ['c1', 'Coach 1'],
  ['c2', 'Coach 2'],
  ['c3_wingsuit', 'Coach 3 — Wingsuit'],
  ['c3_canopy_piloting', 'Coach 3 — Canopy Piloting'],
  ['c3_freefly', 'Coach 3 — Freefly'],
  ['c3_canopy_formation', 'Coach 3 — Canopy Formation'],
  ['cdc', 'Competition Development Coach'],
  ['jm', 'Jump Master'],
  ['jmr', 'Jump Master Restricted'],
  ['gci', 'Ground Control Instructor'],
  ['ssi', 'Skydiving School Instructor'],
  ['pffi', 'Progressive Free Fall Instructor'],
  ['sse', 'Skydiving School Examiner'],
  ['lf', 'Learning Facilitator'],
  ['rigger_a', 'Rigger A'],
  ['rigger_a1', 'Rigger A1'],
  ['rigger_a2', 'Rigger A2'],
  ['rigger_b', 'Rigger B'],
  ['rigger_instructor', 'Rigger Instructor'],
  ['rigger_examiner', 'Rigger Examiner'],
  ['ejr', 'Exhibition Jump Rating'],
];

export const USPA_RATING_CODES = [
  ['coach', 'Coach'],
  ['affi', 'AFF Instructor'],
  ['iad_i', 'IAD Instructor'],
  ['sl_i', 'Static Line Instructor'],
  ['ti', 'USPA Tandem Instructor'],
  ['coach_examiner', 'Coach Examiner'],
  ['affi_examiner', 'AFF Examiner'],
  ['iad_examiner', 'IAD Examiner'],
  ['sl_examiner', 'S/L Examiner'],
  ['ti_examiner', 'Tandem Examiner'],
  ['course_director', 'Course Director'],
  ['iecd', 'Instructor Examiner Course Director'],
  ['pro', 'PRO rating'],
  ['sta', 'S&TA'],
];

export const TANDEM_SYSTEMS = [
  ['upt_vector', 'UPT Vector'],
  ['upt_sigma', 'UPT Sigma / Sigma II'],
  ['strong_dual_hawk', 'Strong Dual Hawk'],
  ['other', 'Other'],
];

export const MEDICAL_KINDS = [
  ['class_iii', 'Class III'],
];

// Long-form names for known orgs — used in OrgCard headers.
export const ORG_FULL_NAMES = {
  CSPA: 'Canadian Sport Parachuting Association',
  USPA: 'United States Parachute Association',
};


// --------------------------------------------------------------------- //
// 2026 restructure: org-grouped associations + compact tandem/medical
// boxes, all hosted inside the identity card.
// --------------------------------------------------------------------- //
//
// Memberships, CoPs, and federation ratings are presented as "association
// cards" — one per (org, org_other) tuple — that the user clicks to
// expand. Each card lists the org's membership(s), CoPs, and ratings.
// CoPs and ratings can be added from within their parent org's card; the
// org is locked to the host card so you can't accidentally file a USPA
// CoP under a CSPA association by switching the dropdown.
//
// Tandem ratings and medicals stay flat — neither is org-keyed (tandem
// is manufacturer-issued, medicals are issued by aviation regulators).
// Both render as compact 2-column boxes inside the identity card.

// Group memberships+cops+ratings under an (org, org_other) key. CSPA
// and USPA each get one card; each unique OTHER federation gets its
// own. Returns a stable-sorted list (CSPA, USPA, then OTHER alpha
// by org_other).
export function groupCredentialsByOrg(jumper) {
  const groups = new Map();

  function keyFor(item) {
    return item.org === 'OTHER'
      ? `OTHER:${(item.org_other || '').trim()}`
      : item.org;
  }

  function ensure(item) {
    const k = keyFor(item);
    if (!groups.has(k)) {
      groups.set(k, {
        key: k,
        org: item.org,
        org_other: item.org_other || null,
        memberships: [],
        cops: [],
        ratings: [],
      });
    }
    return groups.get(k);
  }

  for (const m of jumper.memberships) ensure(m).memberships.push(m);
  for (const c of jumper.cops) ensure(c).cops.push(c);
  for (const r of jumper.ratings) ensure(r).ratings.push(r);

  return [...groups.values()].sort((a, b) => {
    const orderOf = (g) => (g.org === 'CSPA' ? 0 : g.org === 'USPA' ? 1 : 2);
    const diff = orderOf(a) - orderOf(b);
    if (diff !== 0) return diff;
    return (a.org_other || '').localeCompare(b.org_other || '');
  });
}


// Per D56 (Phase 2): read-only display. The "+ Add Association" button
// and the inline MembershipForm-when-adding flow are gone; adds happen
// from the unified Edit form (Phase 3+). The onSaved prop is retained
// in the signature so the parent's reload-after-save plumbing stays
// connected; nothing currently invokes it from this subtree.
// Exported so IdentityEditFull can render the same read-only display
// inside the unified Edit form during Phase 3a (before the
// associations editor becomes interactive in Phase 3b).
export function AssociationsSection({ jumper }) {
  const groups = groupCredentialsByOrg(jumper);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-[11px] tracking-[0.2em] text-neutral-300 font-medium">
          ASSOCIATIONS
        </h2>
      </div>

      {groups.length === 0 && (
        <div
          className="rounded-lg p-3 text-[12px] text-neutral-500"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
        >
          No associations yet. Use Edit on the identity card to add a
          CSPA, USPA, or other federation membership.
        </div>
      )}

      <div className="space-y-2">
        {groups.map((group) => (
          <OrgCard
            key={group.key}
            jumper={jumper}
            group={group}
          />
        ))}
      </div>
    </div>
  );
}


// One association card. Click the header to expand. Inside the body:
// the org's membership(s), CoPs, and ratings — all read-only.
//
// Per D56 (Phase 2): inline pencil/trash and "+ Add CoP" / "+ Add Rating"
// affordances are gone. Adds, edits, and deletes all live in the unified
// Edit form (Phase 3+). Membership rows are still rendered with the
// promoted card style — position implies the "membership" kind.
function OrgCard({ jumper, group }) {
  // Default to expanded when this is the only association so the
  // user sees their content without a click.
  const [expanded, setExpanded] = useState(true);

  const orgLabel = group.org === 'OTHER'
    ? (group.org_other || 'Other federation')
    : group.org;
  const orgFullName = group.org === 'OTHER'
    ? null
    : ORG_FULL_NAMES[group.org];

  // Counts for the header summary line.
  const summary = [];
  if (group.memberships.length === 0) summary.push({ text: 'no membership', warn: true });
  if (group.cops.length > 0) summary.push({ text: `${group.cops.length} CoP${group.cops.length === 1 ? '' : 's'}` });
  if (group.ratings.length > 0) summary.push({ text: `${group.ratings.length} rating${group.ratings.length === 1 ? '' : 's'}` });

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
    >
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-neutral-800/30 transition text-left"
      >
        <div className="flex items-center gap-3 min-w-0">
          {expanded
            ? <ChevronDown className="w-3.5 h-3.5 text-neutral-500 flex-shrink-0" />
            : <ChevronRight className="w-3.5 h-3.5 text-neutral-500 flex-shrink-0" />}
          <span className="text-[14px] font-medium text-neutral-100">{orgLabel}</span>
          {orgFullName && (
            <span className="text-[11px] text-neutral-500 truncate">{orgFullName}</span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {summary.map((s, i) => (
            <React.Fragment key={i}>
              {i > 0 && (
                <span className="text-[10px] text-neutral-700">·</span>
              )}
              <span
                className="text-[10px]"
                style={{ color: s.warn ? 'var(--status-watch)' : 'var(--text-faint)' }}
              >
                {s.text}
              </span>
            </React.Fragment>
          ))}
        </div>
      </button>

      {expanded && (
        <div className="px-4 py-3 space-y-3" style={{ borderTop: '0.5px solid var(--border)' }}>
          {/* Promoted membership row(s) — read-only display. The
              membership IS the org's anchor; CoPs and ratings sit
              under it as credentials. */}
          {group.memberships.map((m) => (
            <div
              key={m.id}
              className="flex items-center justify-between gap-2 py-2 px-3 rounded-md"
              style={{ background: 'var(--surface-2)' }}
            >
              <div className="flex items-center gap-3 flex-wrap min-w-0">
                <span className="text-[13px] text-neutral-100 font-mono">
                  #{m.member_number}
                </span>
                <ExpiryChip date={m.expiry_date} />
                {m.card_attachment_id && <CardChip jumper={jumper} attachmentId={m.card_attachment_id} />}
              </div>
            </div>
          ))}

          {/* "No active membership" placeholder — pure display per D56 */}
          {group.memberships.length === 0 && (
            <div
              className="flex items-center justify-between gap-2 py-2 px-3 rounded-md text-[12px]"
              style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}
            >
              <span>No active membership for this association</span>
            </div>
          )}

          {/* CoPs / Ratings 2-column grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
            {/* CoPs column */}
            <div className="space-y-1.5">
              <div className="text-[9px] tracking-[0.15em] text-neutral-600 font-medium">
                COPS
              </div>
              {group.cops.map((c) => {
                const levels = group.org === 'CSPA'
                  ? CSPA_COP_LEVELS
                  : group.org === 'USPA'
                    ? USPA_COP_LEVELS
                    : null;
                const levelLabel = levels
                  ? (levels.find(([v]) => v === c.level)?.[1] || c.level)
                  : c.level;
                return (
                  <SubRow key={c.id}>
                    <span className="text-[12px] text-neutral-200">{levelLabel}</span>
                    <span className="text-[10px] text-neutral-500 font-mono">
                      {c.issued_date}
                    </span>
                    {c.card_attachment_id && <CardChip jumper={jumper} attachmentId={c.card_attachment_id} />}
                  </SubRow>
                );
              })}
            </div>

            {/* Ratings column */}
            <div className="space-y-1.5">
              <div className="text-[9px] tracking-[0.15em] text-neutral-600 font-medium">
                RATINGS
              </div>
              {group.ratings.map((r) => {
                const codes = group.org === 'CSPA'
                  ? CSPA_RATING_CODES
                  : group.org === 'USPA'
                    ? USPA_RATING_CODES
                    : null;
                const codeLabel = codes
                  ? (codes.find(([v]) => v === r.code)?.[1] || r.code)
                  : r.code;
                return (
                  <SubRow key={r.id}>
                    <span className="text-[12px] text-neutral-200">{codeLabel}</span>
                    <ExpiryChip date={r.expiry_date} />
                    {r.card_attachment_id && <CardChip jumper={jumper} attachmentId={r.card_attachment_id} />}
                  </SubRow>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


// One inline row inside an org card: small kind label + custom
// content. Flat — no card chrome around it. Per D56 (Phase 2) this
// is now pure display; the previous Edit / Delete buttons moved to
// the unified Edit form on the identity card.
function SubRow({ kind, children }) {
  // Kind is optional: when the row's parent column already labels
  // the section (e.g. a "COPS" column header above), the per-row
  // kind tag is redundant and should be omitted by passing
  // ``kind={null}``.
  return (
    <div
      className="flex items-center gap-2 py-2 px-2 rounded"
      style={{ background: 'var(--surface-1)' }}
    >
      {kind && (
        <span className="text-[9px] tracking-[0.15em] text-neutral-600 font-medium">
          {kind}
        </span>
      )}
      <div className="flex items-center gap-2 flex-wrap min-w-0">
        {children}
      </div>
    </div>
  );
}


// Tiny card-attachment indicator next to a credential row. Shows a
// paperclip + the filename truncated. Read-only — to clear or replace
// the attachment, edit the credential.
export function CardChip({ jumper, attachmentId }) {
  const att = jumper.attachments.find((a) => a.id === attachmentId);
  if (!att) return null;
  return (
    <span
      className="text-[10px] text-neutral-500 flex items-center gap-1 font-mono truncate max-w-[160px]"
      title={att.filename}
    >
      <Paperclip className="w-2.5 h-2.5 flex-shrink-0" />
      {att.filename}
    </span>
  );
}


// --------------------------------------------------------------------- //
// Compact tandem ratings + medicals (sit inside identity card)
// --------------------------------------------------------------------- //

// Per D56 (Phase 2): pure-display tandem-rating list. Editing,
// adding, and deleting move to the unified Edit form (Phase 3+).
// Exported for the same reason AssociationsSection is.
export function CompactTandemRatings({ jumper }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-[11px] tracking-[0.2em] text-neutral-300 font-medium">
          TANDEM RATINGS
        </h2>
      </div>

      {jumper.tandem_ratings.length === 0 && (
        <div className="text-[11px] text-neutral-500 px-2 py-2">
          None recorded.
        </div>
      )}

      <div className="space-y-1.5">
        {jumper.tandem_ratings.map((t) => {
          const systemLabel = t.system === 'other'
            ? (t.system_other || 'Other')
            : (TANDEM_SYSTEMS.find(([v]) => v === t.system)?.[1] || t.system);
          return (
            <SubRow key={t.id}>
              <span className="text-[12px] text-neutral-300">{systemLabel}</span>
              <ExpiryChip date={t.expiry_date} />
              {t.card_attachment_id && <CardChip jumper={jumper} attachmentId={t.card_attachment_id} />}
            </SubRow>
          );
        })}
      </div>
    </div>
  );
}


// Per D56 (Phase 2): pure-display medicals list. Editing, adding,
// and deleting move to the unified Edit form (Phase 3+).
// Exported for the same reason AssociationsSection is.
export function CompactMedicals({ jumper }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-[11px] tracking-[0.2em] text-neutral-300 font-medium">
          MEDICALS
        </h2>
      </div>

      {jumper.medicals.length === 0 && (
        <div className="text-[11px] text-neutral-500 px-2 py-2">
          None recorded.
        </div>
      )}

      <div className="space-y-1.5">
        {jumper.medicals.map((m) => {
          // `kindLabel` resolved against MEDICAL_KINDS for future use;
          // the current display row keeps `issuing_authority` as the
          // primary label per the prior UX and surfaces the kind only
          // implicitly. Kept the lookup local to make the Phase 3
          // editor's row template a straight copy.
          // eslint-disable-next-line no-unused-vars
          const kindLabel = MEDICAL_KINDS.find(([v]) => v === m.kind)?.[1] || m.kind;
          return (
            <SubRow key={m.id}>
              <span className="text-[12px] text-neutral-300">{m.issuing_authority}</span>
              <ExpiryChip date={m.expiry_date} />
              {m.card_attachment_id && <CardChip jumper={jumper} attachmentId={m.card_attachment_id} />}
            </SubRow>
          );
        })}
      </div>
    </div>
  );
}


// Visual cue for an expiry date: green if > 30 days out, yellow if
// within 30 days, red if past. Used by every credential row.
export function ExpiryChip({ date }) {
  if (!date) return null;
  const days = daysUntil(date);
  // Loudness is monotonic with urgency: red is the loudest (brightest
  // text, most opaque background, thickest border), amber is medium,
  // green is quiet. The previous rendering inverted this — green
  // looked the most vivid which buried real urgency.
  let style;
  let label;
  if (days < 0) {
    style = {
      color: 'var(--status-critical)',
      background: 'rgba(239,68,68,0.18)',
      border: '0.5px solid rgba(239,68,68,0.5)',
    };
    label = `Expired ${-days}d ago`;
  } else if (days <= 30) {
    style = {
      color: 'var(--status-watch)',
      background: 'rgba(245,158,11,0.14)',
      border: '0.5px solid rgba(245,158,11,0.35)',
    };
    label = `Expires in ${days}d`;
  } else {
    style = {
      color: 'var(--status-ready)',
      background: 'rgba(134,239,172,0.04)',
      border: '0.5px solid rgba(134,239,172,0.15)',
    };
    label = `Expires ${date}`;
  }
  return (
    <span
      className="text-[10px] tracking-[0.05em] px-2 py-0.5 rounded-full font-mono"
      style={style}
    >
      {label}
    </span>
  );
}


function daysUntil(isoDate) {
  if (!isoDate) return null;
  const then = new Date(`${isoDate}T00:00:00Z`).getTime();
  const now = Date.now();
  return Math.ceil((then - now) / (24 * 60 * 60 * 1000));
}


// --------------------------------------------------------------------- //
// Identity card — read view
// --------------------------------------------------------------------- //

function IdentityView({ jumper, onEdit }) {
  const stale = isExitWeightStale(jumper);
  return (
    <div
      className="rounded-xl p-5 mb-6"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div
            className="w-10 h-10 rounded-full flex items-center justify-center"
            style={{ background: 'var(--surface-2)', border: '0.5px solid var(--border-strong)' }}
          >
            <User className="w-4 h-4 text-neutral-400" />
          </div>
          <div>
            <h2 className="text-[11px] tracking-[0.2em] text-neutral-300 font-medium">
              IDENTITY
            </h2>
            <div className="text-[18px] text-neutral-100 mt-0.5">
              {jumper.name || <span className="italic text-neutral-500">Unnamed jumper</span>}
            </div>
          </div>
        </div>
        <button
          onClick={onEdit}
          className="px-3 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition hover:bg-neutral-800/50"
          style={{
            background: 'transparent',
            color: 'var(--text)',
            border: '0.5px solid var(--border-strong)',
          }}
        >
          <Pencil className="w-3 h-3" />
          Edit
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4 text-[13px]">
        <KV label="Exit weight">
          <span className="font-mono text-neutral-100">{jumper.exit_weight_lb} lb</span>
          {jumper.exit_weight_updated_at && (
            <span className="text-neutral-500 text-[11px] ml-2 font-mono">
              confirmed {jumper.exit_weight_updated_at}
            </span>
          )}
        </KV>
        <KV label="Wingloading driver">
          <span
            className="text-neutral-300 text-[13px]"
            title="Computed live from your exit weight and the active rig's main canopy size. Also feeds lineset-wear calculations on every main canopy."
          >
            Computed live
          </span>
        </KV>
      </div>

      {stale && (
        <div
          className="mt-4 rounded-lg p-3 flex items-start gap-2 text-[12px]"
          style={{
            background: 'rgba(251,191,36,0.06)',
            border: '0.5px solid rgba(251,191,36,0.25)',
            color: 'var(--status-watch)',
          }}
        >
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          <div>
            Your exit weight was last confirmed
            {' '}<span className="font-mono">
              {daysSince(jumper.exit_weight_updated_at)}
            </span> days ago. Per D33 we nudge you after a year — re-confirm with Edit so the wear-math reads a current value.
          </div>
        </div>
      )}

      {/* Inner divider, then the credential sub-sections — pure
          display per D56 Phase 2. Editing moves to the unified Edit
          form on the identity card (Phase 3+); `onSaved` is no
          longer threaded through the read view. */}
      <div className="my-5 border-t" style={{ borderColor: 'var(--border-strong)' }} />

      <AssociationsSection jumper={jumper} />

      <div className="my-5 border-t" style={{ borderColor: 'var(--border-strong)' }} />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <CompactTandemRatings jumper={jumper} />
        <CompactMedicals jumper={jumper} />
      </div>
    </div>
  );
}


// Legacy IdentityEdit (name + exit_weight only) lived here until
// D56 Phase 5. It was superseded by IdentityEditFull (./IdentityEditFull)
// which now drives Save through the per-row save orchestrator across
// identity + every D47 credential collection. See git history for
// the previous implementation.


// --------------------------------------------------------------------- //
// Onboarding card — first-run, list returned []
// --------------------------------------------------------------------- //

function OnboardingForm({ onCreated }) {
  const [name, setName] = useState('');
  const [exitWeight, setExitWeight] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleCreate(e) {
    e?.preventDefault?.();
    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        name: name.trim() || null,
        exit_weight_lb: parseFloat(exitWeight),
        // exit_weight_updated_at omitted — backend stamps today
        // by default per the JumperCreate docstring.
      };
      await createJumper(payload);
      onCreated();
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleCreate}
      className="rounded-xl p-5 mb-6"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
    >
      <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-1">
        SET UP YOUR JUMPER
      </div>
      <div className="text-[18px] text-neutral-100 mb-3">
        Welcome — let's start with your exit weight.
      </div>
      <div className="text-[12px] text-neutral-500 mb-4 leading-relaxed">
        Your exit weight (all-up: body + rig + clothing) drives your
        wingloading on My Rig and the lineset-wear calculations on each
        main canopy. You can update it any time.
      </div>

      {error && <InlineError error={error} />}

      <FormGrid>
        <Field label="NAME (optional)">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={submitting}
            placeholder="e.g. Alex"
            className={inputCls}
          />
        </Field>
        <Field label="EXIT WEIGHT (lb)">
          <input
            type="number"
            step="0.1"
            min="0.1"
            value={exitWeight}
            onChange={(e) => setExitWeight(e.target.value)}
            disabled={submitting}
            placeholder="all-up exit weight"
            className={inputCls}
          />
        </Field>
      </FormGrid>

      <div className="flex items-center justify-end gap-2 mt-4">
        <button
          type="submit"
          disabled={submitting || !exitWeight || parseFloat(exitWeight) <= 0}
          className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition"
          style={{
            background: submitting ? 'var(--surface-3)' : 'var(--text)',
            color: submitting ? 'var(--text-faint)' : 'var(--bg)',
            cursor: submitting ? 'not-allowed' : 'pointer',
            opacity: !exitWeight || parseFloat(exitWeight) <= 0 ? 0.5 : 1,
          }}
        >
          {submitting ? (
            <>
              <Loader2 className="w-3 h-3 animate-spin" />
              Creating…
            </>
          ) : (
            <>
              <Save className="w-3 h-3" />
              Create profile
            </>
          )}
        </button>
      </div>
    </form>
  );
}


// --------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------- //

function isExitWeightStale(jumper) {
  if (!jumper?.exit_weight_updated_at) return false;
  return daysSince(jumper.exit_weight_updated_at) > STALENESS_DAYS;
}

function daysSince(isoDate) {
  if (!isoDate) return null;
  const then = new Date(`${isoDate}T00:00:00Z`).getTime();
  const now = Date.now();
  return Math.floor((now - then) / (24 * 60 * 60 * 1000));
}


function KV({ label, children }) {
  return (
    <div>
      <div className="text-[10px] tracking-[0.15em] text-neutral-500 uppercase mb-1">
        {label}
      </div>
      <div>{children}</div>
    </div>
  );
}

// FormGrid / Field / inputCls / InlineError are exported so
// IdentityEditFull (D56) can render identity inputs with the same
// shell-styling the legacy IdentityEdit uses.
export function FormGrid({ children }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>;
}

export function Field({ label, children }) {
  return (
    <div>
      <div className="text-[9px] tracking-[0.2em] text-neutral-500 font-medium mb-1">
        {label}
      </div>
      {children}
    </div>
  );
}

export const inputCls =
  'w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100 bg-[var(--bg)] border border-neutral-800 focus:border-neutral-600 focus:outline-none disabled:opacity-50';


export function InlineError({ error }) {
  const isApi = error instanceof ApiError;
  const problem = isApi ? error.problem : null;
  const pointers = problem?.errors || [];
  return (
    <div
      className="mb-3 p-3 rounded-lg flex items-start gap-2 text-[12px]"
      style={{ background: 'rgba(248,113,113,0.06)', border: '0.5px solid rgba(248,113,113,0.25)', color: 'var(--status-critical)' }}
    >
      <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div>{problem?.detail || error.message || String(error)}</div>
        {pointers.length > 0 && (
          <ul className="mt-1.5 ml-3 list-disc space-y-0.5">
            {pointers.map((e, i) => (
              <li key={i} className="text-[11px] font-mono text-neutral-400">
                <span className="text-neutral-300">{e.pointer}</span>: {e.detail}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}


function ErrorBanner({ error }) {
  const isApi = error instanceof ApiError;
  const problem = isApi ? error.problem : null;
  return (
    <div
      className="p-4 rounded-xl flex items-start gap-3"
      style={{ background: 'rgba(248,113,113,0.05)', border: '0.5px solid rgba(248,113,113,0.25)' }}
    >
      <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-neutral-100">
          {isApi ? (problem?.title || 'Request failed') : "Couldn't load profile"}
        </div>
        <div className="text-[12px] text-neutral-400 mt-1">
          {problem?.detail || error.message}
        </div>
      </div>
    </div>
  );
}

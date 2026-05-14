import React, { useState, useEffect, useRef } from 'react';
import {
  X,
  AlertTriangle,
  Plus,
  Loader2,
  Paperclip,
  ChevronDown,
  ChevronRight,
  MapPin,
  Sparkles,
} from 'lucide-react';
import {
  createJump,
  updateJump,
  listDropzones,
  getDropzone,
  listRigs,
  getMain,
  listPeople,
  createPerson,
  ApiError,
} from '../api';
import {
  useAltitudeUnit,
  metersToDisplay,
  displayToMeters,
  altitudeSuffix,
} from '../units';

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

// 'Tandem' is surfaced as a discipline option for the personal-logbook
// case (passenger or instructor). ``is_tandem`` (D47) is wired through
// JUMP TYPE: selecting ``instructing`` flips the flag — that's the
// instructor-side tandem signal, which is what D47's tandem-currency
// math actually counts.
const DISCIPLINES = ['Belly', 'Freefly', 'Angle', 'Tracking', 'Wingsuit', 'Canopy', 'Tandem', 'Other'];

// D53: closed enum for the role/purpose of a jump. Disjoint from
// ``discipline`` (how you flew). Multi-valued — a camera flyer on
// an angle is one jump with two values. Wire format is the enum
// value (snake_case); the second item in each pair is the display
// label rendered in the chip UI. Tandem stays on ``is_tandem``
// (D47) and is intentionally absent here.
const JUMP_TYPES = [
  ['regular_jump', 'Regular jump'],
  ['coaching', 'Coaching'],
  ['instructing', 'Instructing'],
  ['camera', 'Camera'],
  ['organizing', 'Organizing'],
  ['coached', 'Coached'],
  ['instructed', 'Instructed'],
];

// D57 removed the ``landing_direction`` enum (previously
// ``LANDING_DIRECTIONS`` lived here). Landing accuracy is captured
// as a single magnitude in ``landing_distance_m``; on-target
// landings leave it blank.

// Form-state altitude defaults in the user's DISPLAY unit. The
// form keeps the typed string verbatim while the user edits so
// they can write "13500" and see "13500" — no round-trip through
// meters mangling the value. Conversion to meters (D12 canonical
// wire unit) happens once, at submit time, in ``buildPayload``.
function defaultExitAltitude(altitudeUnit) {
  return altitudeUnit === 'ft' ? '13500' : '4000';
}
function defaultDeploymentAltitude(altitudeUnit) {
  return altitudeUnit === 'ft' ? '3500' : '1100';
}

// Per-discipline freefall model parameters.
//
//   T0  — seconds from exit to reaching terminal velocity (acceleration phase)
//   D0  — meters fallen during the acceleration phase
//   V   — terminal velocity in m/s (constant thereafter)
//
// Sources for the velocities and acceleration distances:
//   * USPA Skydiver's Information Manual (SIM) — body position +
//     terminal velocity ranges
//   * Common belly rule-of-thumb: "10 sec to terminal, then ~5 s
//     per 1000 ft" → 53 m/s ≈ 120 mph terminal
//   * Freefly average across sit-fly (~70 m/s) and head-down
//     (~80 m/s) → 75 m/s ≈ 170 mph
//   * Wingsuit vertical fall rate quoted in flight reports +
//     PIA wingsuit guidance: ~22 m/s ≈ 50 mph (lateral lift
//     bleeds vertical speed; this is the descent component only)
//   * Angle (atmonauti / angle flying) sits between belly and
//     freefly. Steep angles (~45°) approach 70-75 m/s; shallow
//     angles (~30°) closer to 55-60 m/s. Conventional coaching
//     default ~65 m/s ≈ 145 mph for a typical group angle dive.
//   * Tracking — body face-to-earth with marginal lift; vertical
//     fall rate similar to belly with the lift bleed-off. Quoted
//     ranges 45-55 m/s; conservative single-point ~50 m/s.
//
// Numbers are deliberately conservative single-points rather than
// ranges. Users can override by typing the stopwatch reading.
const FREEFALL_PARAMS = {
  Belly:    { t0: 12, d0: 450, v: 53 },
  Freefly:  { t0: 14, d0: 580, v: 75 },
  Angle:    { t0: 13, d0: 520, v: 65 },
  Tracking: { t0: 12, d0: 430, v: 50 },
  Wingsuit: { t0: 5,  d0: 100, v: 22 },
  // Canopy: no entry — there's no freefall to estimate; the
  //   estimator returns null and the UI disables the button.
  Other:    { t0: 12, d0: 450, v: 53 },  // fall back to belly
};

/**
 * Estimate freefall seconds from exit / deployment altitude + discipline.
 *
 * Returns ``null`` when the inputs don't permit an estimate
 * (non-positive drop, missing/unknown discipline that has no
 * params). Otherwise rounds to the nearest second.
 *
 * Canopy is a special case: a "canopy jump" in this app's taxonomy
 * means the jumper deployed essentially at exit (hop-and-pop, CRW,
 * or canopy-piloting work). 5 seconds is the conventional default
 * counted between exit and pulling — a stable, useful number that
 * the user can override per jump.
 */
function estimateFreefallSeconds(discipline, exit_m, deploy_m) {
  if (discipline === 'Canopy') return 5;
  const drop = Number(exit_m) - Number(deploy_m);
  if (!Number.isFinite(drop) || drop <= 0) return null;
  const params = FREEFALL_PARAMS[discipline] || FREEFALL_PARAMS.Other;
  if (!params) return null;
  if (drop <= params.d0) {
    // Short hop-and-pop: terminal velocity isn't reached. Use the
    // kinematic free-fall equation under gravity (g = 9.81 m/s²),
    // which approximates the early acceleration phase well enough
    // for an estimate. Air drag during acceleration would slow it
    // slightly — acceptable error for a "ballpark" hint.
    return Math.round(Math.sqrt((2 * drop) / 9.81));
  }
  // Standard case: full acceleration phase + terminal segment.
  const terminalDistance = drop - params.d0;
  const terminalTime = terminalDistance / params.v;
  return Math.round(params.t0 + terminalTime);
}

// D57 removed the per-jump <environment> override. The jump form's
// ENVIRONMENTS constant lived here previously; the dropzone modal
// keeps its own copy because the DZ is now the single source of
// truth for the wear-math environment value (D44 / D45 §Resolution
// order as amended by D57).

function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// Two modes:
//   'create' — fresh form, multipart POST with attachments support
//   'edit'   — prefilled from initialJump, JSON PUT (D31 metadata-only,
//              no attachment editing — the file picker is hidden in
//              edit mode and the existing attachments are preserved
//              server-side per D31's contract).
export default function LogJumpModal({
  visible,
  onClose,
  onCreated,
  onUpdated,
  suggestedJumpNumber,
  lastDropzone,
  mode = 'create',
  initialJump = null,
}) {
  const isEdit = mode === 'edit' && initialJump != null;
  const [altitudeUnit] = useAltitudeUnit();
  const [form, setForm] = useState({
    jump_number: '',
    title: '',
    date: todayIso(),
    dropzone: '',
    // R.D.5 (D44): structured DZ reference. Set by picker selection;
    // cleared when the user types freeform DZ name. The free-text
    // ``dropzone`` field above stays as a human-readable label.
    dropzone_id: null,
    // R.2.2-light (D33): structured rig reference. Set by the rig
    // picker dropdown; null = "no rig recorded" (fast-log convenience
    // and pre-rig-manager backward compat).
    rig_id: null,
    aircraft: '',
    discipline: '',
    // Stored as the user's DISPLAY value (string, in whatever unit
    // they picked in Settings). Converting only at submit time
    // means typing "13500" stays "13500" — no round-trip through
    // meters at integer precision flickers the input as the user
    // types. Wire format is meters per D12; ``buildPayload``
    // converts via ``displayToMeters`` once.
    exit_altitude: defaultExitAltitude(altitudeUnit),
    deployment_altitude: defaultDeploymentAltitude(altitudeUnit),
    freefall_time_s: '',
    notes: '',
    // R.D.5 (D45 / D57): the per-jump environment override was
    // removed by D57 — the linked DZ is now the single source for
    // the wear-math environment value. ``packed_in_poor_conditions``
    // (Peelman's second modifier) stays behind the Advanced
    // disclosure.
    packed_in_poor_conditions: false,
    // D53 / D57: packer + group facts. ``packed_by`` UUID null ≡
    // self-packed (D54 §Decision). The redundant ``group_size``
    // scalar was removed by D57 — the count is implied by
    // ``len(group_members)``. ``group_members`` is a UUID[] driven
    // by the multi-picker.
    packed_by: null,
    group_members: [],
    // D53 / D57: jump role/purpose (closed multi-valued enum) and
    // landing accuracy. ``jump_types`` is the wire-format string[]
    // — toggling a chip adds/removes the value verbatim. Landing
    // distance is stored as a string for typing friction; converted
    // to float at submit. The directional half
    // (``landing_direction``) was removed by D57; the magnitude
    // alone is the accuracy signal.
    // Pre-select ``regular_jump`` on a new jump (D61 — the
    // majority-case role; the user can deselect or pick another).
    // The edit path overrides this with the persisted value.
    jump_types: ['regular_jump'],
    landing_distance_m: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  // Files staged in the modal but not yet uploaded. They go up with
  // the multipart POST when the user hits Save jump.
  const [files, setFiles] = useState([]);
  const fileInputRef = useRef(null);

  // R.D.5: DZ picker state. ``dropzones`` is the typeahead source,
  // loaded from the API on first open. ``pickerOpen`` controls
  // dropdown visibility; ``advancedOpen`` toggles the per-jump
  // env override + packing checkbox.
  const [dropzones, setDropzones] = useState([]);
  const [dzLoadFailed, setDzLoadFailed] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  // Track whether the DZ list has been loaded for this open of the
  // modal so we don't re-fetch on every render.
  const dzLoadedRef = useRef(false);
  // R.2.2-light.b: rig picker state. ``rigs`` is the dropdown source
  // loaded once per open. ``selectedMain`` carries the resolved
  // current_main_id → Main record so we can display the canopy as
  // an inline chip beside the rig dropdown. ``rigLoadFailed`` is
  // tolerated (form stays usable in no-rig-link mode if the API is
  // unreachable).
  const [rigs, setRigs] = useState([]);
  const [rigLoadFailed, setRigLoadFailed] = useState(false);
  const [selectedMain, setSelectedMain] = useState(null);
  const [mainLoadFailed, setMainLoadFailed] = useState(false);
  const rigsLoadedRef = useRef(false);
  // D54 (Phase 3b): People registry for the packer + group-member
  // pickers. Same load-once-per-open posture as ``dropzones`` and
  // ``rigs`` — failure is tolerated and the form stays usable
  // (packed_by stays null = self-packed; group_members stays empty).
  const [people, setPeople] = useState([]);
  const [peopleLoadFailed, setPeopleLoadFailed] = useState(false);
  const peopleLoadedRef = useRef(false);
  // D53 / D57: the dedicated "group & packer" disclosure
  // (``GroupAndPackerDisclosure``) and its ``detailsOpen`` state
  // hook were removed by D57 alongside the Phase 1 redesign — the
  // surviving fields render in the Advanced disclosure now.
  // R.D.6: aircraft suggestions for the AIRCRAFT typeahead, fetched
  // lazily when the user links a DZ. Keyed by dropzone_id; values
  // are the full Dropzone records' ``aircraft`` arrays. Cached for
  // the lifetime of the open modal.
  const [linkedDZAircraft, setLinkedDZAircraft] = useState([]);
  // R.D.6 fast-log auto-populate: when a DZ is picked, the AIRCRAFT
  // field auto-fills with the first plane in that DZ's fleet. The
  // user can edit it freely; once they type or pick from the
  // dropdown, this ref flips to false and subsequent DZ changes
  // leave their input alone. The ref (rather than form state)
  // keeps the marker out of the wire payload — it's only used to
  // gate UI behavior.
  const aircraftAutoFilledRef = useRef(false);
  // Same ownership-tracking pattern for FREEFALL TIME. The field
  // auto-fills with the discipline-aware estimate whenever the
  // inputs are valid; user typing flips the ref to false so the
  // estimate doesn't overwrite their stopwatch reading. The
  // Estimate button remains as a "reset to formula" escape hatch.
  const freefallAutoFilledRef = useRef(false);
  // Tracks the discipline value at the previous render so we can
  // detect a real change vs. the initial-mount hydrate. ``undefined``
  // means "modal hasn't opened yet" — the first effect pass on open
  // captures the current value as the baseline without overwriting
  // the (possibly hydrated-from-edit-mode) freefall time.
  const previousDisciplineRef = useRef(undefined);

  // Pre-fill the form when the modal opens. Two paths:
  //   * edit: hydrate every field from the initialJump so the user
  //     starts from the existing values and only diffs what changed.
  //   * create: pre-fill jump_number with the server-suggested next
  //     and dropzone with the most-recent dropzone seen.
  useEffect(() => {
    if (!visible) {
      setSubmitting(false);
      setFiles([]);
      setPickerOpen(false);
      setAdvancedOpen(false);
      dzLoadedRef.current = false;
      rigsLoadedRef.current = false;
      peopleLoadedRef.current = false;
      setSelectedMain(null);
      setMainLoadFailed(false);
      aircraftAutoFilledRef.current = false;
      freefallAutoFilledRef.current = false;
      previousDisciplineRef.current = undefined;
      return;
    }
    setError(null);
    if (isEdit) {
      setForm({
        jump_number: String(initialJump.jump_number),
        title: initialJump.title || '',
        date: initialJump.date,
        dropzone: initialJump.dropzone || '',
        dropzone_id: initialJump.dropzone_id || null,
        rig_id: initialJump.rig_id || null,
        aircraft: initialJump.aircraft || '',
        discipline: initialJump.discipline || '',
        // Convert canonical meters from the API into the user's
        // display unit before populating the form.
        exit_altitude: String(
          metersToDisplay(initialJump.exit_altitude_m, altitudeUnit),
        ),
        deployment_altitude: String(
          metersToDisplay(initialJump.deployment_altitude_m, altitudeUnit),
        ),
        freefall_time_s:
          initialJump.freefall_time_s != null ? String(initialJump.freefall_time_s) : '',
        notes: initialJump.notes || '',
        // D57 removed the per-jump <environment> override and the
        // ``group_size`` scalar; neither is hydrated from the
        // existing jump record.
        packed_in_poor_conditions: !!initialJump.packed_in_poor_conditions,
        packed_by: initialJump.packed_by || null,
        group_members: initialJump.group_members || [],
        jump_types: initialJump.jump_types || [],
        landing_distance_m:
          initialJump.landing_distance_m != null
            ? String(initialJump.landing_distance_m)
            : '',
      });
      // If the existing jump carries the packing-conditions flag,
      // expand Advanced so the user sees what's set.
      if (initialJump.packed_in_poor_conditions) {
        setAdvancedOpen(true);
      }
    } else {
      // Full reset on every "Log a new jump" open. We deliberately
      // do NOT spread the previous form state — that lets stale
      // values from a half-finished entry (or from a prior edit
      // session whose modal we just closed) bleed into the next
      // create. The only fields we carry forward are:
      //   * jump_number — server-suggested next number
      //   * dropzone — most-recent DZ name (fast-log convenience)
      // Everything else (title, notes, aircraft, discipline,
      // freefall, env override, etc.) starts blank.
      setForm({
        jump_number: suggestedJumpNumber ? String(suggestedJumpNumber) : '',
        title: '',
        date: todayIso(),
        dropzone: lastDropzone || '',
        dropzone_id: null,
        rig_id: null,
        aircraft: '',
        discipline: '',
        exit_altitude: defaultExitAltitude(altitudeUnit),
        deployment_altitude: defaultDeploymentAltitude(altitudeUnit),
        freefall_time_s: '',
        notes: '',
        // D57 removed ``environment``, ``group_size``, and
        // ``landing_direction`` from the form state.
        packed_in_poor_conditions: false,
        packed_by: null,
        group_members: [],
        // D61: default-select regular_jump on a new jump.
        jump_types: ['regular_jump'],
        landing_distance_m: '',
      });
    }
  }, [visible, isEdit, initialJump, suggestedJumpNumber, lastDropzone, altitudeUnit]);

  // Load the DZ picker options once per open. Failure is tolerated:
  // the form stays usable in free-text-only mode if the API can't
  // be reached (matches LogJumpModal's pre-R.D.5 behavior).
  //
  // D60 preselect: when this is a brand-new jump (``!isEdit``) and
  // the logbook has a starred dropzone, pre-populate
  // ``form.dropzone_id`` so the user doesn't have to pick it every
  // time. The starred flag rides on each DropzoneSummary; read it
  // straight off ``rows`` — no second call. Mirrors the D58 rig
  // preselect pattern below.
  //
  // Only stamp ``dropzone_id`` when it's currently null — a
  // functional setForm guards against a race where the user picks
  // a DZ before the API resolves. The edit path is untouched
  // (``isEdit`` short-circuits): an existing jump's dropzone_id
  // comes from the form-init effect.
  useEffect(() => {
    if (!visible || dzLoadedRef.current) return;
    dzLoadedRef.current = true;
    setDzLoadFailed(false);
    listDropzones({ limit: 1000 })
      .then((rows) => {
        setDropzones(rows);
        if (isEdit) return;
        const starred = rows.find((r) => r.starred);
        if (!starred) return;
        setForm((f) => (f.dropzone_id ? f : { ...f, dropzone_id: starred.id }));
      })
      .catch(() => setDzLoadFailed(true));
  }, [visible, isEdit]);

  // R.2.2-light.b: load the rig dropdown options once per open. Same
  // posture as the DZ load — failure is tolerated; the form stays
  // usable with rig_id=null when the rigs API is unreachable.
  //
  // D58 preselect: when this is a brand-new jump (``!isEdit``) and
  // the logbook has a starred rig, pre-populate ``form.rig_id`` so
  // the user doesn't have to pick it every time. The starred flag
  // rides on each Rig record (RigSummary.starred); we read it
  // straight off ``rows`` without a second call.
  //
  // We deliberately only set ``rig_id`` when it's currently null —
  // a functional setForm guards against a race where the user
  // somehow picks a rig before the API resolves (unlikely with a
  // <100ms localhost call, but cheap to protect against). The edit
  // path is untouched: ``isEdit`` jumps in with the existing
  // jump's rig_id from the form-init effect and we don't override
  // that.
  useEffect(() => {
    if (!visible || rigsLoadedRef.current) return;
    rigsLoadedRef.current = true;
    setRigLoadFailed(false);
    listRigs({ limit: 1000 })
      .then((rows) => {
        setRigs(rows);
        if (isEdit) return;
        const starred = rows.find((r) => r.starred);
        if (!starred) return;
        setForm((f) => (f.rig_id ? f : { ...f, rig_id: starred.id }));
      })
      .catch(() => setRigLoadFailed(true));
  }, [visible, isEdit]);

  // D54 (Phase 3b): load the People registry once per open. Same
  // failure-tolerated posture as the DZ + rig loads.
  useEffect(() => {
    if (!visible || peopleLoadedRef.current) return;
    peopleLoadedRef.current = true;
    setPeopleLoadFailed(false);
    listPeople({ limit: 1000 })
      .then((rows) => setPeople(rows))
      .catch(() => setPeopleLoadFailed(true));
  }, [visible]);

  // R.2.2-light.b: resolve the picked rig's current main canopy so
  // the inline chip can display "manufacturer model — size sqft".
  // Re-runs on every rig_id change. The fetch uses the live current
  // state from the rig record; R.2.3 will swap this for a frozen
  // snapshot resolved against rig-snapshot.xml.
  useEffect(() => {
    if (!visible || !form.rig_id) {
      setSelectedMain(null);
      setMainLoadFailed(false);
      return;
    }
    const pickedRig = rigs.find((r) => r.id === form.rig_id);
    if (!pickedRig || !pickedRig.current_main_id) {
      setSelectedMain(null);
      return;
    }
    let cancelled = false;
    setMainLoadFailed(false);
    getMain(pickedRig.current_main_id)
      .then((main) => {
        if (!cancelled) setSelectedMain(main);
      })
      .catch(() => {
        if (!cancelled) {
          setSelectedMain(null);
          setMainLoadFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [visible, form.rig_id, rigs]);

  // R.D.7 freefall auto-populate. Two effects, two layers of intent:
  //
  //   1. ALTITUDE / unit changes — fill or update only if the field
  //      is empty or was previously auto-filled. User typing flips
  //      ``freefallAutoFilledRef`` to false and locks the field.
  //
  //   2. DISCIPLINE change — always overwrite, regardless of who
  //      owns the value. Picking a new discipline expresses an
  //      intent that the previous estimate (or the user's typed
  //      reading from the prior discipline) no longer applies.
  //      Re-arms the auto-fill ref so subsequent altitude tweaks
  //      stay in sync until the user types again.
  //
  // The two effects share the same estimator and ref but disagree
  // on whether to respect ownership.

  // Effect 2: discipline change — force overwrite.
  useEffect(() => {
    if (!visible) return;
    // First pass after the modal opens captures the current
    // discipline as the baseline so an edit-mode hydrate doesn't
    // immediately overwrite the saved freefall time.
    if (previousDisciplineRef.current === undefined) {
      previousDisciplineRef.current = form.discipline;
      return;
    }
    if (previousDisciplineRef.current === form.discipline) return;
    previousDisciplineRef.current = form.discipline;

    const estimate = estimateFreefallSeconds(
      form.discipline,
      displayToMeters(form.exit_altitude, altitudeUnit),
      displayToMeters(form.deployment_altitude, altitudeUnit),
    );
    if (estimate === null) return;
    freefallAutoFilledRef.current = true;
    setForm((f) => ({ ...f, freefall_time_s: String(estimate) }));
  }, [visible, form.discipline, form.exit_altitude, form.deployment_altitude, altitudeUnit]);

  // Effect 1: altitude / unit change — respect ownership.
  useEffect(() => {
    if (!visible) return;
    // The estimator works in meters (D12 canonical). Convert from
    // the display unit on each call rather than storing meters in
    // form state, which would mangle the user's typed input.
    const estimate = estimateFreefallSeconds(
      form.discipline,
      displayToMeters(form.exit_altitude, altitudeUnit),
      displayToMeters(form.deployment_altitude, altitudeUnit),
    );
    if (estimate === null) return;
    setForm((f) => {
      const fieldEmpty = !f.freefall_time_s || !String(f.freefall_time_s).trim();
      if (fieldEmpty || freefallAutoFilledRef.current) {
        freefallAutoFilledRef.current = true;
        return { ...f, freefall_time_s: String(estimate) };
      }
      return f;
    });
  }, [
    visible,
    form.discipline,
    form.exit_altitude,
    form.deployment_altitude,
    altitudeUnit,
  ]);

  // R.D.6: when the user links a DZ, fetch its aircraft list so the
  // AIRCRAFT field can suggest the DZ's planes AND auto-populate
  // the field for fast logging. Re-fetches on dropzone_id change;
  // clears when the link is dropped. Failure silently leaves the
  // suggestions empty — the field still works as a freeform text
  // input.
  useEffect(() => {
    if (!visible) return;
    if (!form.dropzone_id) {
      setLinkedDZAircraft([]);
      return;
    }
    let cancelled = false;
    getDropzone(form.dropzone_id)
      .then((dz) => {
        if (cancelled) return;
        const fleet = dz.aircraft || [];
        setLinkedDZAircraft(fleet);

        // Fast-log auto-populate: drop the first plane's model into
        // the AIRCRAFT field if the user hasn't taken ownership of
        // it yet. ``aircraftAutoFilledRef`` tracks ownership:
        //   * Empty field → auto-populate (mark as auto)
        //   * Auto-filled previously → overwrite (still auto)
        //   * User typed/picked → leave alone
        // Edit mode hydrates form.aircraft from initialJump BEFORE
        // this effect fires, so the saved aircraft on an existing
        // jump is preserved (the ref starts at false).
        if (fleet.length > 0) {
          const firstModel = fleet[0].model;
          setForm((f) => {
            const fieldEmpty = !f.aircraft || !f.aircraft.trim();
            if (fieldEmpty || aircraftAutoFilledRef.current) {
              aircraftAutoFilledRef.current = true;
              return { ...f, aircraft: firstModel };
            }
            return f;
          });
        }
      })
      .catch(() => {
        if (!cancelled) setLinkedDZAircraft([]);
      });
    return () => { cancelled = true; };
  }, [visible, form.dropzone_id]);

  // Lock body scroll while the modal is open so background scroll
  // doesn't leak through the backdrop.
  useEffect(() => {
    if (visible) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [visible]);

  if (!visible) return null;

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  // Map a form-level state to a JumpCreate-shaped payload. Empty
  // strings on optional fields become null so the backend doesn't see
  // an empty string where it expects a real value or null.
  function buildPayload() {
    return {
      jump_number: parseInt(form.jump_number, 10),
      title: form.title.trim() || null,
      date: form.date,
      dropzone: form.dropzone.trim(),
      // R.D.5 (D44/D45 / D57): structured DZ link + the surviving
      // per-jump wear-math knob. ``packed_in_poor_conditions`` stays;
      // the per-jump ``environment`` override was removed by D57.
      // The DZ link drives the wear-math environment value via its
      // own ``environment`` field now.
      dropzone_id: form.dropzone_id || null,
      // R.2.2-light (D33): rig link. null = no rig recorded for
      // this jump.
      rig_id: form.rig_id || null,
      // Only emit ``true`` when the user actively checked it.
      // ``false`` here would be persisted as a meaningful "I packed
      // in clean conditions" claim — for v0.1 we treat unchecked as
      // "unknown / not stated" and let the wear math fall back.
      packed_in_poor_conditions: form.packed_in_poor_conditions
        ? true
        : null,
      aircraft: form.aircraft.trim() || null,
      discipline: form.discipline || null,
      // Convert display value → meters (D12 wire format) at the
      // payload boundary. ``displayToMeters`` returns null for
      // empty input — but the field is ``required``, so an empty
      // string here is a form bug; let it fail the required check
      // server-side rather than NaN-stuffing the payload.
      exit_altitude_m: displayToMeters(form.exit_altitude, altitudeUnit),
      deployment_altitude_m: displayToMeters(form.deployment_altitude, altitudeUnit),
      freefall_time_s: form.freefall_time_s ? parseInt(form.freefall_time_s, 10) : null,
      notes: form.notes.trim() || null,
      // D53 / D57: packer + group facts. ``packed_by`` null ≡
      // self-packed by D54 §Decision (the logbook owner is never a
      // Person). The redundant ``group_size`` scalar was removed by
      // D57 — the headline count is implied by
      // ``len(group_members)``. ``group_members`` is already a UUID[]
      // from the multi-picker; the API accepts it verbatim.
      packed_by: form.packed_by || null,
      group_members: form.group_members,
      // D53 / D57: jump role/purpose (multi-valued closed enum) and
      // landing accuracy magnitude. ``jump_types`` is already a
      // string[] of wire-format enum values from the chips; sent
      // verbatim. Landing distance parses the string-typed input;
      // empty stays null (canonical on-target). The directional
      // half (``landing_direction``) was removed by D57.
      jump_types: form.jump_types,
      landing_distance_m: form.landing_distance_m
        ? parseFloat(form.landing_distance_m)
        : null,
      // D47: ``is_tandem`` is wired through JUMP TYPE. The form has no
      // dedicated checkbox — selecting ``instructing`` (i.e. "I was the
      // tandem instructor") is the canonical signal that this jump
      // counts toward tandem-instructor currency. Sent as ``true`` or
      // ``null`` (backend coalesces false → null per is_tandem index
      // semantics).
      is_tandem: form.jump_types.includes('instructing') || null,
    };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (isEdit) {
        // D31: PUT is metadata-only. The backend preserves the
        // attachments array from the on-disk jump.xml regardless of
        // what we send, so we just send the JumpUpdate payload.
        const updated = await updateJump(initialJump.id, buildPayload());
        onUpdated(updated);
      } else {
        const created = await createJump(buildPayload(), files);
        onCreated(created);
        // Reset form after a successful create so the next open is fresh.
        setForm({
          jump_number: '',
          title: '',
          date: todayIso(),
          dropzone: lastDropzone || '',
          dropzone_id: null,
          rig_id: null,
          aircraft: '',
          discipline: '',
          exit_altitude: defaultExitAltitude(altitudeUnit),
          deployment_altitude: defaultDeploymentAltitude(altitudeUnit),
          freefall_time_s: '',
          notes: '',
          // D57 removed environment / group_size / landing_direction.
          packed_in_poor_conditions: false,
          packed_by: null,
          group_members: [],
          // D61: default-select regular_jump on the post-submit reset
          // so the next create open mirrors the initial open.
          jump_types: ['regular_jump'],
          landing_distance_m: '',
        });
        setFiles([]);
      }
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  function handleFileSelect(e) {
    const newFiles = Array.from(e.target.files || []);
    // De-dupe by filename — backend will reject duplicates anyway, but
    // it's friendlier to catch it here so the user gets the picker UX
    // hint instead of a 422.
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => f.name));
      const additions = newFiles.filter((f) => !seen.has(f.name));
      return [...prev, ...additions];
    });
    // Clear the input so picking the same file again would re-trigger
    // onChange (browsers skip onChange when the value hasn't changed).
    e.target.value = '';
  }

  function removeFile(index) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }

  return (
    <>
      <div
        onClick={submitting ? undefined : onClose}
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
      />
      <div className="fixed inset-0 z-50 flex items-start justify-center p-6 pointer-events-none overflow-y-auto">
        <form
          onClick={(e) => e.stopPropagation()}
          onSubmit={handleSubmit}
          className="rounded-2xl w-full max-w-2xl pointer-events-auto mt-10 mb-10 flex flex-col"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)', maxHeight: 'calc(100vh - 80px)' }}
        >
          <div
            className="flex items-start justify-between px-5 pt-5 pb-3.5"
            style={{ borderBottom: '0.5px solid var(--border-strong)' }}
          >
            <div>
              <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium mb-1 font-mono">
                {isEdit ? 'EDIT JUMP' : 'NEW JUMP'}
              </div>
              <div className="text-[19px] font-medium tracking-tight">
                {isEdit ? `Edit jump #${initialJump.jump_number}` : 'Log a jump'}
              </div>
              <div className="text-[11px] text-neutral-500 mt-0.5 font-mono">
                {isEdit
                  ? 'Metadata only. Attachments are preserved (D31).'
                  : 'Saves to your logbook folder · validated before write'}
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="w-8 h-8 rounded-lg flex items-center justify-center transition hover:bg-neutral-800"
              style={{ background: 'var(--surface-2)' }}
            >
              <X className="w-3.5 h-3.5 text-neutral-400" />
            </button>
          </div>

          {error && <ErrorBanner error={error} />}

          <div className="overflow-y-auto flex-1 p-5 space-y-3.5">
            {/* Title sits above the identity bands. Optional, full-width,
                inline label. Mirrored into the jump folder name on save. */}
            <InlineBand>
              <InlineField label="TITLE" className="flex-1 min-w-[200px]">
                <input
                  type="text"
                  value={form.title}
                  onChange={update('title')}
                  maxLength={120}
                  className={inputCls}
                  placeholder="Optional — mirrored into the jump folder name"
                />
              </InlineField>
            </InlineBand>

            {/* Band 1: JUMP NO. · DATE · PLACE — inline labels, single row, wraps.
                Widths sized so the inputs aren't squeezed by their own labels:
                a 4-digit jump number needs ~70px of input; an ISO date needs
                ~120px so the browser's native date affordance has room. */}
            <InlineBand>
              <InlineField label="JUMP NO." required className="w-[180px]">
                <input
                  type="number"
                  min={1}
                  required
                  value={form.jump_number}
                  onChange={update('jump_number')}
                  className={inputCls}
                  placeholder="248"
                />
              </InlineField>
              <InlineField label="DATE" required className="w-[230px]">
                <input
                  type="date"
                  required
                  value={form.date}
                  onChange={update('date')}
                  className={inputCls}
                />
              </InlineField>
              <InlineField label="PLACE" required className="flex-1 min-w-[260px]">
                {/* DropzonePicker keeps its own helper + dropdown
                    behavior; the InlineField wrapper just supplies
                    the inline label and width. */}
                <DropzonePicker
                  dropzones={dropzones}
                  dzLoadFailed={dzLoadFailed}
                  form={form}
                  setForm={setForm}
                  pickerOpen={pickerOpen}
                  setPickerOpen={setPickerOpen}
                />
              </InlineField>
            </InlineBand>

            {/* Band 2: AIRCRAFT · EQUIPMENT (rig). Inline labels. */}
            <InlineBand>
              <InlineField label="AIRCRAFT" className="flex-1 min-w-[180px]">
                <AircraftField
                  value={form.aircraft}
                  setValue={(v) => setForm((f) => ({ ...f, aircraft: v }))}
                  suggestions={linkedDZAircraft}
                  onUserSet={() => {
                    aircraftAutoFilledRef.current = false;
                  }}
                />
              </InlineField>
              <InlineField label="EQUIPMENT" className="flex-1 min-w-[180px]">
                <RigPicker
                  rigs={rigs}
                  rigLoadFailed={rigLoadFailed}
                  selectedMain={selectedMain}
                  mainLoadFailed={mainLoadFailed}
                  form={form}
                  setForm={setForm}
                />
              </InlineField>
            </InlineBand>

            {/* Band 3: DISCIPLINE · JUMP TYPE. Inline labels, both with chevron. */}
            <InlineBand>
              <InlineField label="DISCIPLINE" className="flex-1 min-w-[180px]">
                <div className="relative">
                  <select
                    value={form.discipline}
                    onChange={update('discipline')}
                    className={inputCls + ' appearance-none pr-7'}
                  >
                    <option value="">— none —</option>
                    {DISCIPLINES.map((d) => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </select>
                  <ChevronDown className="w-3 h-3 text-neutral-500 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" />
                </div>
              </InlineField>
              <InlineField label="JUMP TYPE" className="flex-1 min-w-[180px]">
                <JumpTypesField
                  value={form.jump_types}
                  onChange={(types) =>
                    setForm((f) => ({ ...f, jump_types: types }))
                  }
                />
              </InlineField>
            </InlineBand>

            {/* Hairline divider separating identity bands from flight data. */}
            <div style={{ height: '1px', background: 'var(--border)' }} />

            {/* Band 4: EXIT ALTITUDE · OPENING ALTITUDE · FREEFALL / TOTAL FREEFALL.
                Stacked labels, 3 equal columns. */}
            <div className="grid grid-cols-3 gap-3">
              <Field
                label={`EXIT ALTITUDE (${altitudeSuffix(altitudeUnit)})`}
                required
              >
                <input
                  type="number"
                  min={0}
                  required
                  value={form.exit_altitude}
                  onChange={update('exit_altitude')}
                  className={inputCls}
                />
              </Field>
              <Field
                label={`OPENING ALTITUDE (${altitudeSuffix(altitudeUnit)})`}
                required
              >
                <input
                  type="number"
                  min={0}
                  required
                  value={form.deployment_altitude}
                  onChange={update('deployment_altitude')}
                  className={inputCls}
                />
              </Field>
              <Field label="FREEFALL / TOTAL FREEFALL">
                <FreefallField
                  value={form.freefall_time_s}
                  onChange={(e) => {
                    update('freefall_time_s')(e);
                    freefallAutoFilledRef.current = false;
                  }}
                  onEstimate={(estimate) => {
                    setForm((f) => ({ ...f, freefall_time_s: String(estimate) }));
                    freefallAutoFilledRef.current = true;
                  }}
                  estimate={estimateFreefallSeconds(
                    form.discipline,
                    displayToMeters(form.exit_altitude, altitudeUnit),
                    displayToMeters(form.deployment_altitude, altitudeUnit),
                  )}
                  discipline={form.discipline}
                  bare
                />
              </Field>
            </div>

            {/* Band 5: DESCRIPTION (full-width textarea) + LANDING DISTANCE
                inline on the same header row. Direction is hidden in this
                slice — state is preserved and submitted as whatever the
                init produces (Phase 1b removes it for real). */}
            <div>
              <div className="flex items-end justify-between gap-3 mb-1.5">
                <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium">
                  DESCRIPTION
                </div>
                <div className="flex items-center gap-2">
                  <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium">
                    LANDING DISTANCE (m)
                  </div>
                  <input
                    type="number"
                    min={0}
                    step="0.1"
                    value={form.landing_distance_m}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        landing_distance_m: e.target.value,
                      }))
                    }
                    className={inputCls + ' w-[90px]'}
                    placeholder="e.g. 12"
                  />
                </div>
              </div>
              <textarea
                value={form.notes}
                onChange={update('notes')}
                className={inputCls}
                style={{ height: '196px', resize: 'vertical' }}
                placeholder="Anything worth remembering about this jump."
              />
            </div>

            {/* Band 6: JUMP WITH · PACKED BY. Stacked labels, 2 columns.
                Previously lived behind the Group & Packer disclosure; now
                surfaced directly. GROUP SIZE moves to Advanced below. */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
                  JUMP WITH
                </div>
                <PersonMultiPicker
                  people={people}
                  setPeople={setPeople}
                  peopleLoadFailed={peopleLoadFailed}
                  value={form.group_members}
                  onChange={(ids) =>
                    setForm((f) => ({ ...f, group_members: ids }))
                  }
                  label={null}
                  emptyHint="Names you flew with. The list grows as you log."
                />
              </div>
              <div>
                <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
                  PACKED BY
                </div>
                <PersonPicker
                  people={people}
                  setPeople={setPeople}
                  peopleLoadFailed={peopleLoadFailed}
                  value={form.packed_by}
                  onChange={(uuid) => setForm((f) => ({ ...f, packed_by: uuid }))}
                  label={null}
                  emptyHint="Leave blank if you packed it yourself."
                  selfChipWhenNull
                />
              </div>
            </div>

            {/* Disclosures: Attachments + Advanced. */}
            {!isEdit && (
              <AttachmentsDisclosure
                files={files}
                fileInputRef={fileInputRef}
                handleFileSelect={handleFileSelect}
                removeFile={removeFile}
                submitting={submitting}
              />
            )}

            <AdvancedDisclosure
              open={advancedOpen}
              setOpen={setAdvancedOpen}
              form={form}
              setForm={setForm}
            />
          </div>

          <div
            className="flex items-center gap-2 px-5 py-3"
            style={{ background: 'var(--surface-1)', borderTop: '0.5px solid var(--border-strong)' }}
          >
            <span className="text-[11px] text-neutral-500 font-mono">
              {isEdit ? (
                <>Folder will rename if title or jump number changed.</>
              ) : (
                <>
                  Will save to{' '}
                  <span className="font-mono font-bold text-neutral-300">
                    jumps/{form.jump_number || '[#]'}/jump.xml
                  </span>
                </>
              )}
            </span>
            <div className="flex-1" />
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="px-3 py-1.5 text-[12px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-40"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition disabled:opacity-50"
              style={{ background: 'var(--text)', color: 'var(--bg)' }}
            >
              {submitting ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Saving…
                </>
              ) : (
                <>
                  <Plus className="w-3.5 h-3.5" strokeWidth={2.2} />
                  {isEdit ? 'Save changes' : 'Save jump'}
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

const inputCls =
  'w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100 bg-[var(--bg)] border border-[var(--border-strong)] focus:border-[#3a3d41] transition outline-none';

function FormGrid({ children }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>;
}

// Phase 1a layout primitives — inline-label band used by the
// identity rows at the top of the form. The label sits beside the
// input rather than above it, matching the approved mockup; the
// band wraps when there isn't enough horizontal room.
function InlineBand({ children }) {
  return <div className="flex flex-wrap items-end gap-3">{children}</div>;
}

function InlineField({ label, required, children, className = '' }) {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div
        className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium font-mono whitespace-nowrap flex-shrink-0"
        style={{ minWidth: '78px' }}
      >
        {label} {required && <span className="text-neutral-300">*</span>}
      </div>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}

// --------------------------------------------------------------------- //
// RigPicker — dropdown of saved rigs + inline main-canopy display
// --------------------------------------------------------------------- //
//
// R.2.2-light.b: a simple <select> populated from GET /api/v1/rigs.
// On pick, the parent fetches the rig's current_main_id and passes
// it back as ``selectedMain`` so we can display a chip alongside.
// "No rig" (empty value) is the default and submits rig_id=null —
// preserves the freeform fast-log path for jumps where the user
// doesn't track gear in the logbook.
//
// Future R.2.3 will wrap this with a frozen-snapshot writer; the
// picker shape stays the same.
function RigPicker({
  rigs,
  rigLoadFailed,
  selectedMain,
  mainLoadFailed,
  form,
  setForm,
}) {
  const onChange = (e) => {
    const value = e.target.value || null;
    setForm((f) => ({ ...f, rig_id: value }));
  };

  // Format the main as a short inline chip. ``size_sqft`` is decimal
  // on the model — using ``Number()`` then ``toLocaleString``-free
  // ``String`` keeps trailing zero stripping consistent with the
  // backend's ``:g`` posture (200 not 200.0). Manufacturer / model
  // are optional on the Main model; fall back gracefully.
  const mainLabel = (() => {
    if (!form.rig_id) return null;
    if (mainLoadFailed) return 'main info unavailable';
    if (!selectedMain) return null;
    const parts = [];
    if (selectedMain.manufacturer) parts.push(selectedMain.manufacturer);
    if (selectedMain.model) parts.push(selectedMain.model);
    let label = parts.join(' ');
    if (selectedMain.size_sqft != null) {
      const sz = String(Number(selectedMain.size_sqft));
      label = label ? `${label} — ${sz} sqft` : `${sz} sqft`;
    }
    return label || 'main canopy on this rig';
  })();

  return (
    <div className="flex flex-col gap-1">
      <div className="relative hover-hint">
        <select
          id="rig-picker"
          value={form.rig_id || ''}
          onChange={onChange}
          disabled={rigLoadFailed && rigs.length === 0}
          className={inputCls + ' appearance-none pr-7'}
        >
          <option value="">— no rig —</option>
          {rigs.map((r) => (
            <option key={r.id} value={r.id}>
              {r.nickname}
            </option>
          ))}
        </select>
        <ChevronDown className="w-3 h-3 text-neutral-500 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" />
        {/* Main-canopy hint is hidden until the user hovers the picker
            for ~700ms — same affordance as the PLACE field's linked
            tooltip. CSS lives in index.css under ``.hover-hint``. */}
        {mainLabel && (
          <div
            className="hover-hint-body absolute left-0 right-0 top-full mt-1 z-20 rounded-md px-2.5 py-1.5 text-[11px] font-mono shadow-lg"
            style={{
              background: 'var(--surface-1)',
              border: '0.5px solid var(--border)',
              color: 'var(--text-muted)',
            }}
            role="tooltip"
            aria-live="polite"
          >
            {mainLoadFailed ? (
              <span className="text-amber-500">{mainLabel}</span>
            ) : (
              <>main · <span className="text-neutral-300">{mainLabel}</span></>
            )}
          </div>
        )}
      </div>
      {rigLoadFailed && rigs.length === 0 && (
        <p className="text-[10px] text-amber-500 font-mono mt-0.5">
          Couldn't load rigs. Logging will save without a rig link.
        </p>
      )}
      {!rigLoadFailed && rigs.length === 0 && (
        <p className="text-[10px] text-neutral-500 font-mono mt-0.5">
          No rigs yet. Create one on the Rigs page to link this jump.
        </p>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- //
// DropzonePicker — combobox-style DZ selector (R.D.5, D44)
// --------------------------------------------------------------------- //
//
// The free-text ``dropzone`` field stays as the canonical human-
// readable label written into ``<jump>/<dropzone>`` in jump.xml.
// On top of it sits a typeahead surfaced from the existing DZ
// records: choosing one sets ``dropzone_id`` (UUID reference) AND
// fills in the text label, so the picker is a shortcut, not a
// replacement. Users can still type any string they like — that
// keeps the freeform-quick-log workflow alive for places that
// don't have a DZ record yet.

function DropzonePicker({
  dropzones,
  dzLoadFailed,
  form,
  setForm,
  pickerOpen,
  setPickerOpen,
}) {
  // Derive the matched-DZ list from current text. When a DZ is
  // already linked, the input shows the linked name and the picker
  // surfaces an "X of N" helper instead of the full list.
  const query = form.dropzone.trim().toLowerCase();
  const filtered = dropzones.filter((d) => {
    if (!query) return true;
    return (
      d.name.toLowerCase().includes(query) ||
      d.city.toLowerCase().includes(query)
    );
  });

  function selectDZ(dz) {
    setForm((f) => ({
      ...f,
      dropzone: dz.name,
      dropzone_id: dz.id,
      // Don't overwrite an explicit per-jump environment override.
      // The DZ's environment is the next-best fallback in the
      // resolution order (D45) but it lives implicitly via the
      // dropzone_id link, not the jump's <environment> field.
    }));
    setPickerOpen(false);
  }

  function clearLink() {
    setForm((f) => ({ ...f, dropzone_id: null }));
  }

  function onTextChange(e) {
    const next = e.target.value;
    // Typing into the field diverges from any prior DZ link.
    setForm((f) => ({
      ...f,
      dropzone: next,
      dropzone_id: f.dropzone_id ? null : f.dropzone_id,
    }));
    setPickerOpen(true);
  }

  // Resolve the currently-linked DZ (if any) to display the env hint.
  const linkedDZ = form.dropzone_id
    ? dropzones.find((d) => d.id === form.dropzone_id)
    : null;

  return (
    <div className="relative">
      <div className="relative hover-hint">
        <input
          type="text"
          required
          value={form.dropzone}
          onChange={onTextChange}
          onFocus={() => setPickerOpen(true)}
          onBlur={() => {
            // Delay so a click on a row registers before close.
            setTimeout(() => setPickerOpen(false), 150);
          }}
          // When linked, reserve room on the right for the "linked" chip
          // so long DZ names don't slide under it.
          className={inputCls + (linkedDZ ? ' pr-[78px]' : '')}
          placeholder="e.g. Skydive Algarve  (type to filter saved dropzones)"
        />
        {linkedDZ && (
          <button
            type="button"
            onClick={clearLink}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium"
            style={{
              background: 'var(--accent-soft)',
              color: 'var(--text-muted)',
              border: '0.5px solid var(--accent)',
            }}
            title="Clear DZ link (keeps the typed name)"
          >
            <MapPin className="w-2.5 h-2.5" strokeWidth={1.8} />
            linked
            <X className="w-2.5 h-2.5 ml-0.5" />
          </button>
        )}
        {/* When linked, the confirmation hint hides until the user hovers
            the input. CSS lives in index.css under ``.hover-hint``. */}
        {linkedDZ && (
          <div
            className="hover-hint-body absolute left-0 right-0 top-full mt-1 z-20 rounded-md px-2.5 py-1.5 text-[11px] font-mono shadow-lg"
            style={{
              background: 'var(--surface-1)',
              border: '0.5px solid var(--border)',
              color: 'var(--text-muted)',
            }}
            role="tooltip"
          >
            Linked to <span className="text-neutral-300">{linkedDZ.name}</span>
            {' · '}
            env <span className="text-neutral-300">{linkedDZ.environment}</span>
            {' (will be used unless overridden in Advanced)'}
          </div>
        )}
      </div>

      {/* Inline helper line: only when NOT linked. The linked-state hint
          moved into the hover tooltip above to keep the row compact. */}
      {!linkedDZ && (
        <div className="text-[10px] text-neutral-500 mt-1 font-mono">
          {dzLoadFailed ? (
            <span className="text-amber-500">
              Couldn't load saved dropzones — typing freeform name is fine.
            </span>
          ) : dropzones.length === 0 ? (
            <>No saved dropzones yet — typing here logs a freeform name.</>
          ) : (
            <>
              {dropzones.length} saved dropzone{dropzones.length === 1 ? '' : 's'} —
              click to link, or just type a new name.
            </>
          )}
        </div>
      )}

      {/* Dropdown of matching DZs. */}
      {pickerOpen && dropzones.length > 0 && (
        <div
          className="absolute left-0 right-0 z-10 mt-1 rounded-lg overflow-hidden shadow-2xl"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
        >
          {filtered.length === 0 ? (
            <div className="px-3 py-2 text-[12px] text-neutral-500 italic">
              No match. Hit Enter to log "{form.dropzone}" as a freeform name.
            </div>
          ) : (
            <div className="max-h-56 overflow-y-auto">
              {filtered.slice(0, 30).map((dz) => {
                const linked = form.dropzone_id === dz.id;
                return (
                  <button
                    key={dz.id}
                    type="button"
                    // Use mouseDown so the click registers BEFORE
                    // the input's onBlur fires (which closes the
                    // dropdown).
                    onMouseDown={(e) => {
                      e.preventDefault();
                      selectDZ(dz);
                    }}
                    className="w-full text-left px-3 py-2 transition hover:bg-neutral-800/60 flex items-baseline justify-between gap-3"
                    style={{
                      background: linked ? 'var(--accent-soft)' : 'transparent',
                      borderBottom: '0.5px solid #1a1a1a',
                    }}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] text-neutral-100 truncate">
                        {dz.name}
                      </div>
                      <div className="text-[11px] text-neutral-500 truncate">
                        {dz.city}, {dz.country}
                      </div>
                    </div>
                    <div className="text-[10px] font-mono text-neutral-500 flex-shrink-0">
                      {dz.environment}
                    </div>
                  </button>
                );
              })}
              {filtered.length > 30 && (
                <div className="px-3 py-1.5 text-[10px] text-neutral-600 italic font-mono">
                  …{filtered.length - 30} more, refine the filter to see them.
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- //
// JumpTypesField — multi-select chip row (D53, Phase 3c)
// --------------------------------------------------------------------- //
//
// Renders a row of toggleable chips for the closed ``JUMP_TYPES``
// enum. Click a chip to add/remove from the list. Empty selection
// is valid — the wrapper element elides on the wire when the list
// is empty (D53 §Decision).

function JumpTypesField({ value, onChange }) {
  const [open, setOpen] = useState(false);

  function toggle(typeValue) {
    onChange(
      value.includes(typeValue)
        ? value.filter((v) => v !== typeValue)
        : [...value, typeValue],
    );
  }

  // Render the selected values as a comma-joined human-readable
  // string in the input. Empty selection shows a placeholder.
  const display = value
    .map((v) => {
      const pair = JUMP_TYPES.find(([val]) => val === v);
      return pair ? pair[1] : v;
    })
    .join(', ');

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        className={inputCls + ' text-left flex items-center justify-between gap-2 cursor-pointer'}
        style={{ minHeight: '30px' }}
      >
        <span className={display ? 'text-neutral-100 truncate' : 'text-neutral-500'}>
          {display || 'Select…'}
        </span>
        <ChevronDown className="w-3 h-3 text-neutral-500 flex-shrink-0" />
      </button>
      {open && (
        <div
          className="absolute left-0 right-0 z-10 mt-1 rounded-lg overflow-hidden shadow-2xl"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
        >
          <div className="max-h-56 overflow-y-auto py-1">
            {JUMP_TYPES.map(([val, label]) => {
              const active = value.includes(val);
              return (
                <button
                  key={val}
                  type="button"
                  // mouseDown so the click registers before the
                  // parent button's onBlur closes the menu.
                  onMouseDown={(e) => {
                    e.preventDefault();
                    toggle(val);
                  }}
                  className="w-full text-left px-3 py-1.5 transition hover:bg-neutral-800/60 flex items-center gap-2"
                >
                  <span
                    className="w-3 h-3 rounded-sm flex items-center justify-center flex-shrink-0"
                    style={{
                      background: active ? 'var(--text)' : 'transparent',
                      border: active ? '0.5px solid var(--text)' : '0.5px solid var(--text-faint)',
                    }}
                  >
                    {active && (
                      <span
                        className="block w-1.5 h-1.5"
                        style={{ background: 'var(--bg)' }}
                      />
                    )}
                  </span>
                  <span className="text-[12px] text-neutral-100">{label}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- //
// PersonPicker — single-select Person picker (D54, Phase 3b)
// --------------------------------------------------------------------- //
//
// Used by ``packed_by`` on the jump form. Same combobox shape as
// DropzonePicker: typeahead filter, click to select, click again
// to clear. Adds a "+ Add: <typed name>" affordance that calls
// createPerson() inline so the user can mint a new packer mid-log
// without leaving the modal.
//
// Empty value (``null``) is the canonical "self-packed" signal per
// D53/D54 — the picker hint reflects that.

function _shortUnknownLabel(uuid) {
  // Mirrors the backend resolve_person_names fallback (D54 §Decision)
  // so the UI shows the same label for a stale ref the API would
  // return — keeping the round-trip transparent.
  return `Unknown person ${String(uuid).slice(0, 8)}`;
}

function PersonPicker({
  people,
  setPeople,
  peopleLoadFailed,
  value,
  onChange,
  label,
  emptyHint,
  // Phase 1a follow-up: when truthy, render a visible "self" chip
  // inline on the left of the input whenever ``value`` is null —
  // matching the chip-inside-input pattern used by jump_with's
  // multi-picker. Semantically still null (D54: null = self-packed).
  selfChipWhenNull,
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const inputRef = useRef(null);
  const [createError, setCreateError] = useState(null);

  const selected = value
    ? people.find((p) => p.id === value) || { id: value, name: _shortUnknownLabel(value) }
    : null;

  const trimmed = query.trim();
  const lower = trimmed.toLowerCase();
  const filtered = people.filter((p) => {
    if (!lower) return true;
    return p.name.toLowerCase().includes(lower);
  });
  const exactMatch = trimmed
    ? people.some((p) => p.name.toLowerCase() === lower)
    : false;
  const canQuickAdd = trimmed && !exactMatch && !creating;

  function selectPerson(p) {
    onChange(p.id);
    setQuery('');
    setOpen(false);
    setCreateError(null);
  }

  function clear() {
    onChange(null);
    setQuery('');
    setCreateError(null);
  }

  async function handleQuickAdd() {
    if (!canQuickAdd) return;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await createPerson({ name: trimmed });
      // Insert into the parent's people list so subsequent renders
      // (including the multi-picker for group_members) see the new
      // record. NOCASE-aware sort to mirror the backend listing.
      setPeople((prev) =>
        [...prev, { id: created.id, name: created.name }].sort((a, b) =>
          a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
        ),
      );
      onChange(created.id);
      setQuery('');
      setOpen(false);
    } catch (e) {
      setCreateError(e?.message || 'Could not create person');
    } finally {
      setCreating(false);
    }
  }

  // Chip-inside-input layout: selected person (or "self" default) sits
  // on the left of the input frame; the search input fills the rest of
  // the row. Mirrors PersonMultiPicker visually so packed_by reads the
  // same as jump_with.
  const showSelfChip = !!selfChipWhenNull && !selected;

  return (
    <div className="relative">
      {label && (
        <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
          {label}
        </div>
      )}

      <div
        className="w-full rounded-md px-2 py-1 min-h-[32px] bg-[var(--bg)] border border-[var(--border-strong)] focus-within:border-[#3a3d41] transition flex items-center gap-1.5 flex-wrap cursor-text"
        onMouseDown={(e) => {
          // Clicks on empty wrapper area focus the inner input;
          // clicks on chips/buttons fall through naturally.
          if (e.target === e.currentTarget) {
            e.preventDefault();
            inputRef.current?.focus();
          }
        }}
      >
        {selected && (
          <span
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px]"
            style={{
              background: 'var(--accent-soft)',
              color: 'var(--text)',
              border: '0.5px solid var(--accent)',
            }}
          >
            {selected.name}
            <button
              type="button"
              onClick={clear}
              className="hover:text-amber-400 transition"
              title="Clear"
            >
              <X className="w-2.5 h-2.5" />
            </button>
          </span>
        )}
        {showSelfChip && (
          // D54: null = self-packed. The chip is a visual default,
          // not a removable selection — override by typing a name.
          <span
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] italic"
            style={{
              background: 'rgba(160,180,210,0.06)',
              color: 'var(--text-muted)',
              border: '0.5px solid rgba(160,180,210,0.18)',
            }}
          >
            self
          </span>
        )}
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            // Delay so a click on a row registers before close.
            setTimeout(() => setOpen(false), 150);
          }}
          className="flex-1 min-w-[120px] bg-transparent border-0 outline-none text-[13px] text-neutral-100 py-0.5"
          placeholder={
            selected
              ? 'Type a name to replace'
              : showSelfChip
              ? 'Type a name to override'
              : 'Type a name to search or add'
          }
        />
      </div>

      <div className="text-[10px] text-neutral-500 mt-1 font-mono">
        {peopleLoadFailed ? (
          <span className="text-amber-500">
            Couldn't load people — try again or leave blank.
          </span>
        ) : selected || showSelfChip ? null : (
          emptyHint
        )}
        {createError && (
          <span className="ml-2 text-amber-500">· {createError}</span>
        )}
      </div>

      {open && (
        <div
          className="absolute left-0 right-0 z-10 mt-1 rounded-lg overflow-hidden shadow-2xl"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
        >
          <div className="max-h-56 overflow-y-auto">
            {filtered.slice(0, 30).map((p) => (
              <button
                key={p.id}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectPerson(p);
                }}
                className="w-full text-left px-3 py-2 transition hover:bg-neutral-800/60"
                style={{ borderBottom: '0.5px solid #1a1a1a' }}
              >
                <div className="text-[13px] text-neutral-100 truncate">
                  {p.name}
                </div>
              </button>
            ))}
            {filtered.length === 0 && !canQuickAdd && (
              <div className="px-3 py-2 text-[12px] text-neutral-500 italic">
                {trimmed ? 'No match.' : 'No saved people yet.'}
              </div>
            )}
            {canQuickAdd && (
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  handleQuickAdd();
                }}
                disabled={creating}
                className="w-full text-left px-3 py-2 transition hover:bg-neutral-800/60 flex items-center gap-2"
                style={{ borderTop: '0.5px solid #1a1a1a' }}
              >
                {creating ? (
                  <Loader2 className="w-3 h-3 animate-spin text-neutral-400" />
                ) : (
                  <Plus className="w-3 h-3 text-neutral-400" />
                )}
                <span className="text-[12px] text-neutral-200">
                  Add "<span className="text-neutral-50">{trimmed}</span>"
                </span>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- //
// PersonMultiPicker — multi-select Person picker with chips (D54, Phase 3b)
// --------------------------------------------------------------------- //
//
// Used by ``group_members``. Selected People appear as chips above
// the input; the dropdown lists the remaining (not-yet-selected)
// matches. Same quick-add affordance as PersonPicker. Empty
// selection leaves an empty UUID[] in the form state, which the
// payload mapper sends as ``[]`` — the backend treats absent and
// empty equivalently for the wrapper-elide round-trip.

function PersonMultiPicker({
  people,
  setPeople,
  peopleLoadFailed,
  value,
  onChange,
  label,
  emptyHint,
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);
  const inputRef = useRef(null);

  const selected = value
    .map((id) => people.find((p) => p.id === id) || { id, name: _shortUnknownLabel(id) });

  const trimmed = query.trim();
  const lower = trimmed.toLowerCase();
  const available = people.filter((p) => {
    if (value.includes(p.id)) return false;
    if (!lower) return true;
    return p.name.toLowerCase().includes(lower);
  });
  const exactMatch = trimmed
    ? people.some((p) => p.name.toLowerCase() === lower)
    : false;
  const canQuickAdd = trimmed && !exactMatch && !creating;

  function addPerson(p) {
    onChange([...value, p.id]);
    setQuery('');
    setOpen(false);
    setCreateError(null);
  }

  function removePerson(id) {
    onChange(value.filter((v) => v !== id));
  }

  async function handleQuickAdd() {
    if (!canQuickAdd) return;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await createPerson({ name: trimmed });
      setPeople((prev) =>
        [...prev, { id: created.id, name: created.name }].sort((a, b) =>
          a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
        ),
      );
      onChange([...value, created.id]);
      setQuery('');
      setOpen(false);
    } catch (e) {
      setCreateError(e?.message || 'Could not create person');
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="relative">
      {label && (
        <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
          {label}
        </div>
      )}
      <div
        className="w-full rounded-md px-2 py-1 min-h-[32px] bg-[var(--bg)] border border-[var(--border-strong)] focus-within:border-[#3a3d41] transition flex items-center gap-1.5 flex-wrap cursor-text"
        onMouseDown={(e) => {
          // Clicks on empty wrapper area focus the inner input.
          if (e.target === e.currentTarget) {
            e.preventDefault();
            inputRef.current?.focus();
          }
        }}
      >
        {selected.map((p) => (
          <span
            key={p.id}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px]"
            style={{
              background: 'var(--accent-soft)',
              color: 'var(--text)',
              border: '0.5px solid var(--accent)',
            }}
          >
            {p.name}
            <button
              type="button"
              onClick={() => removePerson(p.id)}
              className="hover:text-amber-400 transition"
              title="Remove"
            >
              <X className="w-2.5 h-2.5" />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          className="flex-1 min-w-[120px] bg-transparent border-0 outline-none text-[13px] text-neutral-100 py-0.5"
          placeholder={selected.length === 0 ? 'Type a name to add' : 'Add another'}
        />
      </div>

      <div className="text-[10px] text-neutral-500 mt-1 font-mono">
        {peopleLoadFailed ? (
          <span className="text-amber-500">Couldn't load people — try again later.</span>
        ) : selected.length > 0 ? null : (
          emptyHint
        )}
        {createError && (
          <span className="ml-2 text-amber-500">· {createError}</span>
        )}
      </div>

      {open && (
        <div
          className="absolute left-0 right-0 z-10 mt-1 rounded-lg overflow-hidden shadow-2xl"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
        >
          <div className="max-h-56 overflow-y-auto">
            {available.slice(0, 30).map((p) => (
              <button
                key={p.id}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  addPerson(p);
                }}
                className="w-full text-left px-3 py-2 transition hover:bg-neutral-800/60"
                style={{ borderBottom: '0.5px solid #1a1a1a' }}
              >
                <div className="text-[13px] text-neutral-100 truncate">
                  {p.name}
                </div>
              </button>
            ))}
            {available.length === 0 && !canQuickAdd && (
              <div className="px-3 py-2 text-[12px] text-neutral-500 italic">
                {trimmed
                  ? 'No remaining matches.'
                  : selected.length === people.length && people.length > 0
                  ? 'All saved people are added.'
                  : 'No saved people yet.'}
              </div>
            )}
            {canQuickAdd && (
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  handleQuickAdd();
                }}
                disabled={creating}
                className="w-full text-left px-3 py-2 transition hover:bg-neutral-800/60 flex items-center gap-2"
                style={{ borderTop: '0.5px solid #1a1a1a' }}
              >
                {creating ? (
                  <Loader2 className="w-3 h-3 animate-spin text-neutral-400" />
                ) : (
                  <Plus className="w-3 h-3 text-neutral-400" />
                )}
                <span className="text-[12px] text-neutral-200">
                  Add "<span className="text-neutral-50">{trimmed}</span>" to your people
                </span>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- //
// GroupAndPackerDisclosure removed by D57 (Phase 1b). The dedicated
// disclosure was orphaned by the Phase 1a layout — its surviving
// fields (``packed_by``, ``group_members``) render in the Advanced
// disclosure now. ``group_size`` was removed entirely; the count is
// implied by ``len(group_members)``.
// --------------------------------------------------------------------- //

// --------------------------------------------------------------------- //
// AttachmentsDisclosure — collapsible file picker (Phase 1a)
// --------------------------------------------------------------------- //
//
// The on-create attachment workflow now lives behind a disclosure to
// match the rest of the modal's rare-knobs pattern. Helper line +
// dashed "Add files…" button + per-file chip strip render only when
// the disclosure is open. Edit mode hides the whole disclosure
// (D31: attachments are preserved server-side on PUT).

function AttachmentsDisclosure({
  files,
  fileInputRef,
  handleFileSelect,
  removeFile,
  submitting,
}) {
  const [open, setOpen] = useState(files.length > 0);
  return (
    <div
      className="rounded-lg"
      style={{ background: 'var(--bg)', border: '0.5px solid var(--border)' }}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left transition hover:bg-neutral-900/40"
      >
        {open ? (
          <ChevronDown className="w-3 h-3 text-neutral-500" strokeWidth={1.8} />
        ) : (
          <ChevronRight className="w-3 h-3 text-neutral-500" strokeWidth={1.8} />
        )}
        <span className="text-[12px] text-neutral-300">Attachments</span>
        {files.length > 0 && (
          <span className="ml-auto text-[10px] font-mono text-neutral-500">
            {files.length} file{files.length === 1 ? '' : 's'}
          </span>
        )}
      </button>
      {open && (
        <div
          className="px-3 pb-3 pt-1 space-y-1.5"
          style={{ borderTop: '0.5px solid var(--border)' }}
        >
          {files.length === 0 && (
            <div className="text-[12px] text-neutral-500 italic pt-2">
              Optional. FlySight CSV, video, photos — they go straight into the jump folder.
            </div>
          )}
          {files.map((f, i) => (
            <div
              key={`${f.name}-${i}`}
              className="flex items-center gap-2.5 px-3 py-1.5 rounded-md"
              style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}
            >
              <Paperclip className="w-3 h-3 text-neutral-500 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-neutral-100 truncate font-mono">{f.name}</div>
                <div className="text-[10px] text-neutral-500 font-mono mt-0.5">
                  {f.type || 'unknown type'} · {formatBytes(f.size)}
                </div>
              </div>
              <button
                type="button"
                onClick={() => removeFile(i)}
                disabled={submitting}
                className="w-6 h-6 rounded transition flex items-center justify-center hover:bg-neutral-800 flex-shrink-0"
                title="Remove"
              >
                <X className="w-3 h-3 text-neutral-400" />
              </button>
            </div>
          ))}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={handleFileSelect}
            style={{ display: 'none' }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={submitting}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] text-neutral-300 transition hover:bg-neutral-800/50 disabled:opacity-40"
            style={{ background: 'transparent', border: '0.5px dashed var(--text-faint)' }}
          >
            <Plus className="w-3 h-3" />
            Add files…
          </button>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- //
// AdvancedDisclosure — per-jump env override + packing flag (R.D.5, D45)
// --------------------------------------------------------------------- //
//
// Collapsed by default. Most jumps inherit env from the linked DZ
// (or fall back per D45 resolution order). Expand only when the
// jump deviates from the DZ's normal conditions — a sand patch
// jump at a grass DZ, packing on a beach demo, etc.

function AdvancedDisclosure({ open, setOpen, form, setForm }) {
  // Phase 1a follow-up: GROUP SIZE removed — implied by the jump_with
  // person count. ENVIRONMENT per-jump override removed — line wear
  // now uses the DZ's environment exclusively (D45 resolution order
  // collapses to: linked-DZ env → canopy default → clean_grass).
  // Phase 1b will drop the state keys, payload fields, XSD nodes, and
  // supersede the relevant parts of D45/D53 with a new D-entry.
  const hasOverride = form.packed_in_poor_conditions;

  return (
    <div
      className="rounded-lg"
      style={{ background: 'var(--bg)', border: '0.5px solid var(--border)' }}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left transition hover:bg-neutral-900/40"
      >
        {open ? (
          <ChevronDown className="w-3 h-3 text-neutral-500" strokeWidth={1.8} />
        ) : (
          <ChevronRight className="w-3 h-3 text-neutral-500" strokeWidth={1.8} />
        )}
        <span className="text-[12px] text-neutral-300">Advanced</span>
        {hasOverride && (
          <span className="ml-auto text-[10px] font-mono text-amber-500">
            override active
          </span>
        )}
      </button>

      {open && (
        <div
          className="px-3 pb-3 pt-1 space-y-3"
          style={{ borderTop: '0.5px solid var(--border)' }}
        >
          {/* Packing flag. */}
          <label className="flex items-start gap-2.5 cursor-pointer pt-2">
            <input
              type="checkbox"
              checked={form.packed_in_poor_conditions}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  packed_in_poor_conditions: e.target.checked,
                }))
              }
              className="mt-0.5"
              style={{ accentColor: 'var(--accent)' }}
            />
            <div className="flex-1 min-w-0">
              <div className="text-[12px] text-neutral-200">
                Packed in poor conditions{' '}
                <span className="text-[10px] font-mono text-neutral-500">
                  +0.20 lb
                </span>
              </div>
              <div className="text-[10px] text-neutral-500 mt-0.5 leading-relaxed">
                Windy day, beach demo, dusty packing mat — independent of where
                the jump itself happened.
              </div>
            </div>
          </label>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- //
// AircraftField — combobox with suggestions from linked DZ (R.D.6)
// --------------------------------------------------------------------- //
//
// Stays freeform: user can type anything. When a DZ is linked AND
// has aircraft, focus opens a small dropdown of that DZ's planes.
// Picking a row sets the field to ``model`` (or
// ``model (tail_number)`` if the user wants the disambiguator —
// the formatted variant clicks into the input as-is).

function AircraftField({ value, setValue, suggestions, onUserSet }) {
  const [open, setOpen] = useState(false);

  // Filter suggestions by current input. Both model and tail
  // number are searched; matches are case-insensitive.
  const query = value.trim().toLowerCase();
  const filtered = (suggestions || []).filter((p) => {
    if (!query) return true;
    return (
      p.model.toLowerCase().includes(query) ||
      (p.tail_number || '').toLowerCase().includes(query)
    );
  });

  function pick(plane, withTail) {
    const next = withTail && plane.tail_number
      ? `${plane.model} (${plane.tail_number})`
      : plane.model;
    setValue(next);
    onUserSet?.();
    setOpen(false);
  }

  // ``open`` is set unconditionally on focus; the dropdown render
  // is independently gated on ``filtered.length > 0`` below. This
  // matters in edit mode: the linked-DZ aircraft list arrives
  // asynchronously after `getDropzone(id)` resolves. If we gated
  // setOpen on a `hasSuggestions` check sampled at focus time, the
  // dropdown would stay closed when suggestions land later, and
  // the user would have to re-focus the field. Always-open-on-focus
  // means the dropdown materializes naturally as soon as the
  // suggestions render.

  return (
    <div className="relative">
      <input
        type="text"
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          // Typing is the user taking ownership — flip the
          // auto-fill marker so subsequent DZ changes don't
          // overwrite this input.
          onUserSet?.();
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        className={inputCls}
        placeholder={
          (suggestions || []).length > 0
            ? 'Type or pick from DZ fleet…'
            : 'Optional'
        }
      />
      {open && filtered.length > 0 && (
        <div
          className="absolute left-0 right-0 z-10 mt-1 rounded-lg overflow-hidden shadow-2xl"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
        >
          <div className="max-h-44 overflow-y-auto">
            {filtered.slice(0, 12).map((p, i) => (
              <button
                key={i}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  // Default click drops just the model — clean UX
                  // for the most common case. Hold the tail number
                  // separately if needed.
                  pick(p, false);
                }}
                className="w-full text-left px-3 py-2 transition hover:bg-neutral-800/60 flex items-center justify-between gap-3"
                style={{ borderBottom: '0.5px solid #1a1a1a' }}
              >
                <span className="text-[13px] text-neutral-100">{p.model}</span>
                {p.tail_number && (
                  <span className="text-[10px] font-mono text-neutral-500">
                    {p.tail_number}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- //
// FreefallField — number input + Estimate button (R.D.7)
// --------------------------------------------------------------------- //
//
// Plain number input plus a small "Estimate" button that fills the
// field with a discipline-aware estimate derived from the exit and
// deployment altitudes. Disabled with an explanatory tooltip when
// the inputs don't permit an estimate (Canopy discipline, missing
// altitudes, drop ≤ 0). The user can override the estimate by
// typing — same field, no separate edit mode.

function FreefallField({ value, onChange, onEstimate, estimate, discipline, bare }) {
  const canEstimate = estimate !== null && estimate !== undefined;
  const reason = !canEstimate
    ? 'Set discipline + valid exit / deployment altitudes first.'
    : discipline === 'Canopy'
    ? '≈ 5 s default for canopy jumps (CRW, hop-and-pop, canopy piloting). Click to reset to default.'
    : `≈ ${estimate} s based on ${discipline || 'Belly (default)'} terminal velocity. Click to reset to estimate.`;
  const body = (
    <div className="relative">
      <input
        type="number"
        min={0}
        value={value}
        onChange={onChange}
        className={inputCls}
        placeholder={canEstimate ? `≈ ${estimate}s — click ✨ to fill` : 'Optional'}
      />
      <button
        type="button"
        disabled={!canEstimate}
        onClick={() => canEstimate && onEstimate(estimate)}
        className="absolute right-1.5 top-1/2 -translate-y-1/2 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium transition disabled:opacity-30 disabled:cursor-not-allowed"
        style={{
          background: canEstimate
            ? 'var(--accent-soft)'
            : 'transparent',
          color: canEstimate ? 'var(--text-muted)' : 'var(--text-faint)',
          border: canEstimate
            ? '0.5px solid var(--accent)'
            : '0.5px solid var(--border)',
        }}
        title={reason}
      >
        <Sparkles className="w-2.5 h-2.5" strokeWidth={1.8} />
        {canEstimate ? `≈ ${estimate}s` : 'estimate'}
      </button>
    </div>
  );
  if (bare) return body;
  return <Field label="FREEFALL TIME (S)">{body}</Field>;
}

// RadioCard removed by D57 (Phase 1b) — it was only used by the
// per-jump environment radio cards, which D57 removed alongside
// the field.

function Field({ label, required, children }) {
  return (
    <label className="block">
      <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
        {label} {required && <span className="text-neutral-300">*</span>}
      </div>
      {children}
    </label>
  );
}

function ErrorBanner({ error }) {
  const isApi = error instanceof ApiError;
  const problem = isApi ? error.problem : null;
  const fieldErrors = problem?.errors || [];
  return (
    <div
      className="m-5 mb-0 p-4 rounded-xl flex items-start gap-3"
      style={{
        background: 'rgba(248,113,113,0.05)',
        border: '0.5px solid rgba(248,113,113,0.25)',
      }}
    >
      <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-neutral-100">
          {isApi ? (problem?.title || 'Validation failed') : "Couldn't save"}
        </div>
        {problem?.detail && (
          <div className="text-[12px] text-neutral-400 mt-1">{problem.detail}</div>
        )}
        {!isApi && (
          <div className="text-[12px] text-neutral-400 mt-1">{error.message}</div>
        )}
        {fieldErrors.length > 0 && (
          <ul className="mt-2 space-y-0.5">
            {fieldErrors.map((fe, i) => (
              <li key={i} className="text-[11px] text-neutral-500 font-mono">
                <span className="text-neutral-400">{fe.pointer}</span>: {fe.detail}
              </li>
            ))}
          </ul>
        )}
        {isApi && problem?.type && (
          <div className="text-[11px] text-neutral-500 mt-2 font-mono">
            type: {problem.type} · status: {problem.status}
            {error.requestId && <> · request: {error.requestId}</>}
          </div>
        )}
      </div>
    </div>
  );
}

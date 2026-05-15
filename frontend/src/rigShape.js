// Adapter: real Rig + components → denormalized shape that the
// MyRig + Inventory views were originally written against (mock.js).
//
// This is a transitional layer. Phase 1 lets the existing UI render
// against real data without rewriting every component. Phase 2 (D39
// AAD rules + D33 repack-due math) replaces the placeholder status /
// countdowns / wingloading values with real computation — likely
// migrating those into computed_field on the backend response models
// rather than recomputing on every client render. When that happens,
// this module shrinks to a pure denormalizer.
//
// Caveats carried forward to Phase 2:
//   * Status colors are placeholder (all green unless we have a hard
//     "missing component" signal). Real status comes from D39 + D33.
//   * Repack countdowns (uspaDays / cspaDays) are computed from
//     repack_history[-1].date + jurisdiction; this is a best-effort
//     v0.1 stub of D33's repack clock spec. Returns null when the
//     rig has no repack history yet.
//   * Wingloading is jumper.exit_weight_lb / main.size_sqft. Returns
//     null when either is missing.
//   * Lineset wear (`remaining` percent) is now real per D46 (W.1)
//     for the seed term: consumed_lb = jumps_on_lineset_initial × 1.0
//     baseline, residual_lb = breaking_strength − consumed. The
//     consumed_lb_derived term (per-jump from rig-snapshots, the
//     full Peelman formula in D45) is NOT yet wired — that's R.4.
//     So in v0.1 the bar moves with the seed but doesn't accumulate
//     per-jump after the lineset is installed. Good enough to drive
//     the "go see your rigger" nudge per D46's posture; R.4 lands
//     the per-jump precision once the SQLite index can serve it.

const MS_PER_DAY = 24 * 60 * 60 * 1000;

// D33 repack windows.
const USPA_REPACK_DAYS = 180;
const CSPA_REPACK_DAYS = 270;

// D45/D46 baseline wear per pre-logbook jump (the seed term). Each
// migrated jump consumes 1.0 lb of budget — Peelman's clean-grass
// no-RDS no-poor-packing baseline. Modifiers (env / RDS / packing)
// only apply to in-logbook jumps where we have the per-jump signals;
// for migrated jumps we don't, so we treat them as baseline.
const PER_JUMP_BASELINE_LB = 1.0;

// 2026-05 design-system rule: lineset status is a function of the
// jumper's exit weight and the projected wear rate, not absolute
// strength. Critical when current strength has dropped below the
// jumper's exit weight; watch when the projection puts us within
// 50 jumps of crossing that threshold; ready otherwise.
const WATCH_JUMPS_THRESHOLD = 50;


function asMonthYear(isoDate) {
  if (!isoDate) return null;
  // Mock used "MM/YYYY" or just "YYYY". Match that.
  const parts = isoDate.split('-');
  if (parts.length < 2) return isoDate;
  return `${parts[1]}/${parts[0]}`;
}


function daysBetween(a, b) {
  // a, b: Date objects. Returns floor((b - a) / day).
  return Math.floor((b - a) / MS_PER_DAY);
}


function repackCountdown(rig, jurisdictionLetter, today) {
  // jurisdictionLetter is 'USPA' or 'CSPA'. Returns days remaining
  // until the next repack is due, or null when:
  //   * the rig isn't sealed under that jurisdiction
  //   * the rig has no repack_history yet
  // Negative numbers mean "overdue by N days" — the UI styles
  // negative values as red.
  if (rig.jurisdiction !== jurisdictionLetter && rig.jurisdiction !== 'both') {
    return null;
  }
  if (!rig.repack_history || rig.repack_history.length === 0) return null;
  const latest = rig.repack_history[rig.repack_history.length - 1];
  if (!latest.date) return null;
  const window =
    jurisdictionLetter === 'USPA' ? USPA_REPACK_DAYS : CSPA_REPACK_DAYS;
  const lastRepack = new Date(latest.date + 'T00:00:00Z');
  const due = new Date(lastRepack.getTime() + window * MS_PER_DAY);
  return daysBetween(today, due);
}


function wingloading(jumper, main) {
  if (!jumper || !main) return null;
  if (jumper.exit_weight_lb == null || main.size_sqft == null) return null;
  if (main.size_sqft <= 0) return null;
  return jumper.exit_weight_lb / main.size_sqft;
}


// Component shape adapters — each takes a real entity (or null when
// the rig references an id that no longer exists) and returns the
// denormalized "card" shape MyRig's ComponentCard expects.

// Compute D45 lineset wear from a current_lineset record + the
// active jumper's exit weight (D46: live-read, not snapshotted).
// The backend stamps ``jumps_on_lineset_total = initial + derived``
// on the response per D35; this function consumes that pre-summed
// number directly. In v0.1 the derived term approximates "jumps
// since the lineset was installed" as "jumps logged on the rig"
// (per-jump lineset attribution via rig-snapshot is R.4); when no
// swap / reline occurs the approximation matches the rig's count
// exactly. Older payloads (or hand-edited fixtures) without the
// derived term fall back to ``jumps_on_lineset_initial``.
// Returns the denormalized shape MyRig consumes — see the helper
// comments at the top of the file for the v0.1 caveats.
function shapeLineset(currentLineset, jumper) {
  if (!currentLineset) {
    return {
      type: '—',
      remaining: 0,
      jumps: 0,
      installed: null,
      residual_lb: 0,
      breaking_strength_lb: 0,
      starting_budget_lb: 0,
      consumed_lb: 0,
      jumps_until_critical: null,
      status: 'red',
    };
  }
  const ls = currentLineset;
  const breaking = ls.breaking_strength_lb || 0;
  const seedJumps = ls.jumps_on_lineset_total != null
    ? ls.jumps_on_lineset_total
    : (ls.jumps_on_lineset_initial || 0);

  // D46: total consumed = seed × baseline + consumed_lb_derived.
  // consumed_lb_derived (per-jump Peelman accumulation from
  // rig-snapshots) requires the SQLite index and isn't wired in
  // v0.1 — R.4 territory. So consumed_lb is just the seed term.
  const consumedLb = seedJumps * PER_JUMP_BASELINE_LB;

  // D46 starting budget — informational and the visualization
  // denominator. Live-read the jumper's exit weight; if no jumper
  // record exists yet, fall back to 0 so the bar is still
  // meaningful (% then degrades smoothly toward 0 as consumed →
  // breaking).
  const exitWeight = jumper?.exit_weight_lb ?? 0;
  const startingBudget = Math.max(0, breaking - exitWeight);

  // Residual breaking strength of the lineset right now (lb).
  const residualLb = Math.max(0, breaking - consumedLb);

  // % of original breaking strength remaining. Wear progression —
  // length of the bar in the UI.
  const remainingPct = breaking > 0
    ? Math.max(0, Math.min(100, (residualLb / breaking) * 100))
    : 0;

  // Jumps remaining before residual_lb drops below exit_weight,
  // projecting forward at PER_JUMP_BASELINE_LB per jump. When the
  // jumper hasn't registered an exit weight yet there's no critical
  // threshold to compute against — fall back to a large number so
  // the status reads as 'ready'.
  let jumpsUntilCritical;
  if (exitWeight > 0 && PER_JUMP_BASELINE_LB > 0) {
    jumpsUntilCritical = Math.max(
      0,
      Math.floor((residualLb - exitWeight) / PER_JUMP_BASELINE_LB),
    );
  } else {
    jumpsUntilCritical = Infinity;
  }

  // Jumps-based status: critical when current strength is below the
  // jumper's exit weight; watch when projected to cross that line
  // within the next WATCH_JUMPS_THRESHOLD jumps; ready otherwise.
  let linesetStatus;
  if (exitWeight > 0 && residualLb < exitWeight) {
    linesetStatus = 'red';
  } else if (jumpsUntilCritical <= WATCH_JUMPS_THRESHOLD) {
    linesetStatus = 'yellow';
  } else {
    linesetStatus = 'green';
  }

  return {
    type: ls.line_type,
    installed: ls.install_date,
    jumps: seedJumps,                  // displayed: jumps on lineset
    remaining: Math.round(remainingPct * 10) / 10,  // 1 dp
    residual_lb: Math.round(residualLb * 10) / 10,
    breaking_strength_lb: breaking,
    starting_budget_lb: startingBudget,
    consumed_lb: Math.round(consumedLb * 10) / 10,
    jumps_until_critical:
      Number.isFinite(jumpsUntilCritical) ? jumpsUntilCritical : null,
    status: linesetStatus,
  };
}


function shapeMain(main, jumper) {
  if (!main) {
    return {
      id: null,
      brand: '—',
      model: 'missing',
      size: null,
      jumps: 0,
      dom: null,
      lineset: shapeLineset(null, jumper),
      status: 'red',
      notes: [],
    };
  }
  // D35 per-component jump count: backend supplies
  // ``jump_count_total = jump_count_initial + jump_count_derived``.
  // Fall back to ``jump_count_initial`` for older payloads where
  // the derived term hasn't been wired (e.g. legacy fixtures).
  const jumps = main.jump_count_total != null
    ? main.jump_count_total
    : (main.jump_count_initial || 0);
  return {
    id: main.id,
    brand: main.manufacturer || '—',
    model: main.model || 'unknown',
    size: main.size_sqft,
    serial: main.serial,
    jumps,
    dom: asMonthYear(main.date_of_manufacture),
    lineset: shapeLineset(main.current_lineset, jumper),
    status: main.status === 'active' ? 'green' : 'yellow',
    notes: (main.notes_log || []).map((n) => ({
      date: n.at && n.at.slice(0, 10),
      author: '—',
      content: n.text,
    })),
  };
}


function shapeReserve(reserve) {
  if (!reserve) {
    return {
      id: null,
      brand: '—',
      model: 'missing',
      size: null,
      dom: null,
      repacks: 0,
      repackLimit: 40,
      rides: 0,
      rideLimit: 25,
      status: 'red',
      notes: [],
    };
  }
  return {
    id: reserve.id,
    brand: reserve.manufacturer || '—',
    model: reserve.model || 'unknown',
    size: reserve.size_sqft,
    serial: reserve.serial,
    dom: asMonthYear(reserve.date_of_manufacture),
    repacks: reserve.repack_count_initial || 0,
    repackLimit: reserve.repack_limit || 40,
    rides: reserve.ride_count_initial || 0,
    rideLimit: reserve.ride_limit || 25,
    status: reserve.status === 'active' ? 'green' : 'yellow',
    notes: (reserve.notes_log || []).map((n) => ({
      date: n.at && n.at.slice(0, 10),
      author: '—',
      content: n.text,
    })),
  };
}


function shapeAad(aad) {
  if (!aad) {
    return {
      id: null,
      brand: '—',
      model: 'missing',
      mode: '—',
      dom: null,
      jumps: 0,
      fires: 0,
      nextAction: '—',
      daysToAction: null,
      status: 'red',
      modeMatch: true,
      notes: [],
    };
  }
  return {
    id: aad.id,
    brand: aad.manufacturer || '—',
    model: aad.model || 'unknown',
    mode: aad.mode || '—',
    dom: asMonthYear(aad.date_of_manufacture),
    jumps: aad.jump_count_total != null
      ? aad.jump_count_total
      : (aad.jump_count_initial || 0),
    fires: aad.fire_count_initial || 0,
    // Phase 2 (D39): real service-window / EOL lookup. For now,
    // a placeholder that lights up the UI without lying about a
    // specific date.
    nextAction: 'pending D39 wiring',
    daysToAction: null,
    status: aad.status === 'active' ? 'green' : 'yellow',
    // Phase 2 surfaces wingloading-driven mode mismatch; for now
    // assume match.
    modeMatch: true,
    notes: (aad.notes_log || []).map((n) => ({
      date: n.at && n.at.slice(0, 10),
      author: '—',
      content: n.text,
    })),
  };
}


function shapeContainer(container) {
  if (!container) {
    return {
      id: null,
      brand: '—',
      model: 'missing',
      serial: null,
      dom: null,
      jumps: 0,
      status: 'red',
      notes: [],
    };
  }
  return {
    id: container.id,
    brand: container.manufacturer || '—',
    model: container.model || 'unknown',
    serial: container.serial,
    dom: asMonthYear(container.date_of_manufacture),
    jumps: container.jump_count_total != null
      ? container.jump_count_total
      : (container.jump_count_initial || 0),
    status: container.status === 'active' ? 'green' : 'yellow',
    notes: (container.notes_log || []).map((n) => ({
      date: n.at && n.at.slice(0, 10),
      author: '—',
      content: n.text,
    })),
  };
}


// Aggregate the four component statuses into the rig's overall
// status. Phase 1 placeholder: red if any component is red, yellow
// if any is yellow, otherwise green. Phase 2 (D33 status colors)
// will weight repack-due windows and AAD service windows in here.
function aggregateStatus(componentShapes) {
  if (componentShapes.some((c) => c.status === 'red')) return 'red';
  if (componentShapes.some((c) => c.status === 'yellow')) return 'yellow';
  return 'green';
}


// Build the upcoming-actions list from whatever signals we have.
// Surfaces lineset-reline projections (when status is watch/critical)
// and repack countdowns. Phase 2 will add AAD service windows /
// battery replacement / EOL.
function buildActions(rig, uspaDays, cspaDays, mainShape) {
  const actions = [];
  // Lineset reline projection — only surfaces when the main lineset
  // is within 50 jumps of critical, matching the design-system
  // upcoming-actions list in the My rig redesign mockup.
  const ls = mainShape && mainShape.lineset;
  if (ls && ls.status === 'red') {
    actions.push({
      kind: 'lineset',
      text: 'Main lineset reline',
      detail: 'below exit weight · do not jump',
      level: 'critical',
    });
  } else if (ls && ls.status === 'yellow' && ls.jumps_until_critical != null) {
    actions.push({
      kind: 'lineset',
      text: 'Main lineset reline',
      detail: `~${ls.jumps_until_critical} jumps to critical`,
      level: 'warning',
    });
  }
  if (uspaDays != null) {
    actions.push({
      date: '', // Phase 2: format the actual due date.
      text: 'USPA repack due',
      days: uspaDays,
      level: uspaDays < 30 ? 'warning' : 'info',
    });
  }
  if (cspaDays != null) {
    actions.push({
      date: '',
      text: 'CSPA repack due',
      days: cspaDays,
      level: cspaDays < 30 ? 'warning' : 'info',
    });
  }
  return actions;
}


// Look up a component by id from a list, returning null on miss
// (orphaned ref — the rig references an id that doesn't exist).
function findById(list, id) {
  if (!id || !list) return null;
  return list.find((x) => x.id === id) || null;
}


/**
 * Convert a real Rig + the four lookup lists into the denormalized
 * shape MyRig.jsx and Inventory.jsx consume.
 *
 * @param {object} rig - Real Rig record from /api/v1/rigs.
 * @param {object} lookups - { mains, reserves, aads, containers, jumper, today }.
 *   - mains/reserves/aads/containers: arrays from the corresponding
 *     list endpoints. Each component carries the D35 derived /
 *     total jump counts populated server-side.
 *   - jumper: a Jumper record (used for wingloading). Optional.
 *   - today: a Date for repack countdowns. Defaults to now.
 * @returns {object} denormalized rig
 */
export function buildRigShape(rig, lookups) {
  const today = (lookups && lookups.today) || new Date();
  const main = findById(lookups.mains, rig.current_main_id);
  const reserve = findById(lookups.reserves, rig.current_reserve_id);
  const aad = findById(lookups.aads, rig.current_aad_id);
  const container = findById(lookups.containers, rig.current_container_id);

  // Per-component jump counts are server-derived per D35
  // (jump_count_total = initial + derived); no client-side jumps
  // filter is needed. The previous pre-D35-wiring implementation
  // walked listJumps and counted ``j.rig_id === rig.id`` here.
  const mainShape = shapeMain(main, lookups.jumper);
  const reserveShape = shapeReserve(reserve);
  const aadShape = shapeAad(aad);
  const containerShape = shapeContainer(container);

  const uspaDays = repackCountdown(rig, 'USPA', today);
  const cspaDays = repackCountdown(rig, 'CSPA', today);
  const wl = wingloading(lookups.jumper, main);

  return {
    id: rig.id,
    name: rig.nickname,
    jurisdiction: rig.jurisdiction,
    // D58: passthrough the starred flag so the carousel can render
    // a star indicator and the LogJumpModal (Phase 3) can preselect.
    // Falsy when the backend rig predates D58 / has no <starred>
    // element on disk.
    starred: Boolean(rig.starred),
    uspaDays,
    cspaDays,
    wingloading: wl == null ? 0 : wl,
    status: aggregateStatus([mainShape, reserveShape, aadShape, containerShape]),
    main: mainShape,
    reserve: reserveShape,
    aad: aadShape,
    container: containerShape,
    actions: buildActions(rig, uspaDays, cspaDays, mainShape),
  };
}


// --------------------------------------------------------------------- //
// Inventory shape — flat list of components, all kinds combined.
// --------------------------------------------------------------------- //

/**
 * Flatten the four inventory lists into the shape Inventory.jsx
 * consumes. Each entry carries:
 *   { id, type, brand, model, size?, serial, dom, jumps?, status,
 *     assigned: rig nickname or null }
 *
 * @param {object} lists - { mains, reserves, aads, containers }
 * @param {Array} rigs   - real Rig records for resolving ``assigned``
 *   labels (component.assigned_rig_id → rig.nickname).
 */
export function buildInventoryShape(lists, rigs) {
  const rigById = {};
  for (const r of rigs || []) rigById[r.id] = r;

  const out = [];
  for (const m of lists.mains || []) {
    const s = shapeMain(m);
    out.push({
      ...s,
      type: 'main',
      assigned: m.assigned_rig_id ? rigById[m.assigned_rig_id]?.nickname || null : null,
    });
  }
  for (const r of lists.reserves || []) {
    const s = shapeReserve(r);
    out.push({
      ...s,
      type: 'reserve',
      assigned: r.assigned_rig_id ? rigById[r.assigned_rig_id]?.nickname || null : null,
    });
  }
  for (const a of lists.aads || []) {
    const s = shapeAad(a);
    out.push({
      ...s,
      type: 'aad',
      assigned: a.assigned_rig_id ? rigById[a.assigned_rig_id]?.nickname || null : null,
    });
  }
  for (const c of lists.containers || []) {
    const s = shapeContainer(c);
    out.push({
      ...s,
      type: 'container',
      assigned: c.assigned_rig_id ? rigById[c.assigned_rig_id]?.nickname || null : null,
    });
  }
  return out;
}

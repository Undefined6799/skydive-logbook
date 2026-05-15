// API client for the FastAPI backend.
//
// Default is empty string — relative URLs, same-origin as wherever the
// page was loaded from. This works in two modes without code changes:
//
//   * Packaged / one-app mode: the React app is served by FastAPI at
//     http://localhost:8000/. Relative URL `/api/v1/jumps` resolves to
//     http://localhost:8000/api/v1/jumps. Same origin, no CORS preflight.
//
//   * Vite dev mode: the React app is served by Vite at
//     http://localhost:5173/. Vite's dev-server proxy (vite.config.js)
//     forwards `/api/*` to http://localhost:8000. Same origin from the
//     browser's perspective, no CORS.
//
// VITE_API_BASE in .env still wins if you need to point at a remote
// backend or non-standard port.

const API_BASE = import.meta.env.VITE_API_BASE || '';

// RFC 9457 problem+json envelope (D16). Errors thrown here carry the full
// problem object so UI code can render type, code, errors[], and the
// X-Request-Id from the response headers for support tickets.
export class ApiError extends Error {
  constructor(problem, requestId) {
    super(problem.title || problem.detail || 'Request failed');
    this.problem = problem;
    this.requestId = requestId;
  }
}

async function handle(res) {
  const requestId = res.headers.get('X-Request-Id');
  if (res.status === 204) return null;
  let body;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    throw new ApiError(body || { status: res.status, title: res.statusText }, requestId);
  }
  return body;
}

// `cache: 'no-store'` keeps WKWebView (macOS pywebview backend) and other
// WebView caches from serving a stale GET response after we POST a new
// resource. The backend doesn't emit Cache-Control headers, so without
// this hint a same-URL GET right after a successful POST can come back
// from disk cache with the pre-POST list.
const noStoreInit = { cache: 'no-store' };

export async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/health`, { ...noStoreInit, method: 'GET' });
    return res.ok;
  } catch {
    return false;
  }
}

// GET /api/v1/jumps?limit&offset
// Returns list[JumpSummary]: { id, jump_number, title, date, dropzone }
export async function listJumps({ limit = 100, offset = 0 } = {}) {
  const res = await fetch(`${API_BASE}/api/v1/jumps?limit=${limit}&offset=${offset}`, noStoreInit);
  return handle(res);
}

// GET /api/v1/jumps/{id}
// Returns full Jump including attachments and audit timestamps.
export async function getJump(id) {
  const res = await fetch(`${API_BASE}/api/v1/jumps/${encodeURIComponent(id)}`, noStoreInit);
  return handle(res);
}

// GET /api/v1/jumps/{id}/files
// Returns every user-facing file in the jump folder, marking which
// are tracked (in jump.xml) vs untracked (added via the OS file manager).
export async function listJumpFiles(id) {
  const res = await fetch(`${API_BASE}/api/v1/jumps/${encodeURIComponent(id)}/files`, noStoreInit);
  return handle(res);
}

// POST /api/v1/jumps/{id}/attachments/track  (D41)
// Adopts files already in the folder into jump.xml + SHA256SUMS.
// filenames is an array of filenames already on disk in the jump folder.
// Returns the updated full Jump.
export async function trackJumpFiles(id, filenames) {
  const res = await fetch(`${API_BASE}/api/v1/jumps/${encodeURIComponent(id)}/attachments/track`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filenames }),
  });
  return handle(res);
}

// POST /api/v1/jumps/{id}/attachments  (D42)
// Uploads new files and appends them to the jump's attachments.
// Same multipart shape as createJump's files arg. Returns updated Jump.
export async function addJumpAttachments(id, files) {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  const res = await fetch(`${API_BASE}/api/v1/jumps/${encodeURIComponent(id)}/attachments`, {
    method: 'POST',
    body: fd,
  });
  return handle(res);
}

// DELETE /api/v1/jumps/{id}/attachments/{filename}  (D43)
// Removes a single tracked attachment from the jump's manifest and
// unlinks the file from disk. Returns the updated Jump.
export async function deleteJumpAttachment(id, filename) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumps/${encodeURIComponent(id)}/attachments/${encodeURIComponent(filename)}`,
    { method: 'DELETE' },
  );
  return handle(res);
}

// POST /api/v1/jumps  (multipart/form-data per D30)
// payload: object matching JumpCreate (jump_number, date, dropzone, etc.)
// files: FileList | File[] — optional
export async function createJump(payload, files = []) {
  const fd = new FormData();
  fd.append('jump', JSON.stringify(payload));
  for (const f of files) fd.append('files', f);
  const res = await fetch(`${API_BASE}/api/v1/jumps`, { method: 'POST', body: fd });
  return handle(res);
}

// PUT /api/v1/jumps/{id}
// JSON body matching JumpUpdate (full replace, metadata only per D31).
export async function updateJump(id, payload) {
  const res = await fetch(`${API_BASE}/api/v1/jumps/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// DELETE /api/v1/jumps/{id}
// Soft-deletes to .trash/ per D19.
export async function deleteJump(id) {
  const res = await fetch(`${API_BASE}/api/v1/jumps/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  return handle(res);
}

// GET /api/v1/verify
// Runs the D25 integrity walk. Returns
// { folders_scanned, clean, issues: [{folder, kind, detail}] }.
export async function runVerify() {
  const res = await fetch(`${API_BASE}/api/v1/verify`, noStoreInit);
  return handle(res);
}

// POST /api/v1/reindex
// Rebuilds the SQLite index from XML on disk. Returns
// { folders_scanned, jumps_indexed, skipped, timestamp_fallbacks, aborted, clean }.
export async function runReindex() {
  const res = await fetch(`${API_BASE}/api/v1/reindex`, { method: 'POST' });
  return handle(res);
}

// GET /api/v1/stats
// Returns career-wide jump aggregations:
// { total, this_year, last_90_days, days_since_last_jump, freefall_seconds,
//   year_by_month: [12 ints], by_discipline: [[name, count], ...],
//   by_dropzone: [[name, count], ...] }.
export async function getStats() {
  const res = await fetch(`${API_BASE}/api/v1/stats`, noStoreInit);
  return handle(res);
}

// GET /api/v1/updates/check
// User-initiated lookup against GitHub Releases for the configured repo
// (Settings.update_check_repo on the backend). Returns
// { status, current, latest, release_url, detail } where status is one of
// 'up_to_date', 'update_available', 'no_releases', 'rate_limited', 'error'.
//
// When the backend has no repo configured, the endpoint returns a 503
// `update_check_disabled` problem+json — handle() throws ApiError, and
// the caller checks `err.problem?.code === 'update_check_disabled'` to
// hide the button rather than surface an error to the user.
export async function checkForUpdates() {
  const res = await fetch(`${API_BASE}/api/v1/updates/check`, noStoreInit);
  return handle(res);
}

// --------------------------------------------------------------------- //
// Dropzones (D44)
// --------------------------------------------------------------------- //

// GET /api/v1/dropzones?limit&offset
// Returns list[DropzoneSummary]: { id, name, city, country, environment }.
export async function listDropzones({ limit = 100, offset = 0 } = {}) {
  const res = await fetch(
    `${API_BASE}/api/v1/dropzones?limit=${limit}&offset=${offset}`,
    noStoreInit,
  );
  return handle(res);
}

// GET /api/v1/dropzones/{id}
// Full Dropzone including province, notes, audit timestamps.
export async function getDropzone(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/dropzones/${encodeURIComponent(id)}`,
    noStoreInit,
  );
  return handle(res);
}

// POST /api/v1/dropzones
// JSON body matching DropzoneCreate.
export async function createDropzone(payload) {
  const res = await fetch(`${API_BASE}/api/v1/dropzones`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// PUT /api/v1/dropzones/{id}
// JSON body matching DropzoneUpdate (full replace).
export async function updateDropzone(id, payload) {
  const res = await fetch(`${API_BASE}/api/v1/dropzones/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// DELETE /api/v1/dropzones/{id}
// Soft-deletes to .trash/dropzones/. No cascade to jumps that reference
// the trashed dropzone_id.
export async function deleteDropzone(id) {
  const res = await fetch(`${API_BASE}/api/v1/dropzones/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  return handle(res);
}


// PUT /api/v1/dropzones/{id}/star (D60)
// Star a dropzone as the default for the jump-log form. Idempotent —
// PUT'ing the same id twice returns 200 both times with the same
// Dropzone. The server clears the prior starred DZ atomically under
// the writer lock so the "exactly one starred" invariant holds. No
// DELETE counterpart: the star moves only by starring another DZ or
// by deleting the starred DZ (auto-elects a successor server-side
// via most-recently-jumped, alphabetical fallback). Mirrors starRig.
// Errors:
//   404 — dropzone not found (or soft-deleted)
//   200 — success; response body is the updated Dropzone
export async function starDropzone(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/dropzones/${encodeURIComponent(id)}/star`,
    { method: 'PUT' },
  );
  return handle(res);
}


// --------------------------------------------------------------------- //
// People (D54)
// --------------------------------------------------------------------- //
//
// People are jumpers the owner has flown with (referenced from
// <jump>/<group_members>) and packers (referenced from
// <jump>/<packed_by>). Single registry serves both contexts; the
// logbook owner is NOT a Person — packed_by absent ≡ self-packed.
// Stale references render as "Unknown person <short-uuid>" via the
// service-level resolver (see backend's resolve_person_names).

// GET /api/v1/people?limit&offset
// Returns list[PersonSummary]: { id, name }. Sorted alphabetically
// by name (case-insensitive) at the SQLite layer — pickers can call
// this on every keystroke without paying a parse cost.
export async function listPeople({ limit = 100, offset = 0 } = {}) {
  const res = await fetch(
    `${API_BASE}/api/v1/people?limit=${limit}&offset=${offset}`,
    noStoreInit,
  );
  return handle(res);
}

// GET /api/v1/people/{id}
// Full Person including notes and audit timestamps.
export async function getPerson(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/people/${encodeURIComponent(id)}`,
    noStoreInit,
  );
  return handle(res);
}

// POST /api/v1/people
// JSON body matching PersonCreate ({ name, notes? }). The server
// mints the UUID, NFC-normalizes the name (D4), and stamps audit
// timestamps. The picker's "+ new" affordance calls this to mint a
// Person mid-log without leaving the modal.
export async function createPerson(payload) {
  const res = await fetch(`${API_BASE}/api/v1/people`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// PUT /api/v1/people/{id}
// JSON body matching PersonUpdate (full replace).
export async function updatePerson(id, payload) {
  const res = await fetch(`${API_BASE}/api/v1/people/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// DELETE /api/v1/people/{id}
// Soft-deletes to .trash/people/. No cascade to jumps that reference
// the trashed UUID — soft resolution renders stale refs as
// "Unknown person <short-uuid>" (D54 §Decision).
export async function deletePerson(id) {
  const res = await fetch(`${API_BASE}/api/v1/people/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  return handle(res);
}


// --------------------------------------------------------------------- //
// Rigs (D33+D37+D38, R.2.0c.iv)
// --------------------------------------------------------------------- //

// GET /api/v1/rigs?limit&offset
// Returns list[Rig] — full records including the four current_*_id refs.
export async function listRigs({ limit = 100, offset = 0 } = {}) {
  const res = await fetch(
    `${API_BASE}/api/v1/rigs?limit=${limit}&offset=${offset}`,
    noStoreInit,
  );
  return handle(res);
}

// GET /api/v1/rigs/{id}
export async function getRig(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/rigs/${encodeURIComponent(id)}`,
    noStoreInit,
  );
  return handle(res);
}

// POST /api/v1/rigs
// JSON body matching RigCreate: { nickname, jurisdiction,
// current_main_id, current_reserve_id, current_aad_id,
// current_container_id, repack_history?, notes_log? }.
// Backend validates each component ref (exists + active +
// unassigned) per D37; conflicts surface as RFC 9457 problems.
export async function createRig(payload) {
  const res = await fetch(`${API_BASE}/api/v1/rigs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// PUT /api/v1/rigs/{id}
export async function updateRig(id, payload) {
  const res = await fetch(`${API_BASE}/api/v1/rigs/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// DELETE /api/v1/rigs/{id}
// Cascade clears assigned_rig_id on the four assigned components
// per D37.
export async function deleteRig(id) {
  const res = await fetch(`${API_BASE}/api/v1/rigs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  return handle(res);
}

// POST /api/v1/rigs/reorder (D59)
// Rewrite the carousel order. ``ids`` is the desired left-to-right
// ordering; ids[0] becomes the leftmost rig (display_order=0). The
// server validates that the list is exactly the set of non-trashed
// rig ids — a missing, unknown, or duplicate id returns 422 with
// a #/rig_ids FieldError pointer.
// Errors:
//   422 — list doesn't match the on-disk set
//   200 — success; response body is the reordered list[Rig]
export async function reorderRigs(ids) {
  const res = await fetch(`${API_BASE}/api/v1/rigs/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rig_ids: ids }),
  });
  return handle(res);
}


// PUT /api/v1/rigs/{id}/star (D58)
// Star a rig as the default for the jump-log form. Idempotent — PUT'ing
// the same id twice returns 200 both times with the same Rig. The
// server clears the prior starred rig atomically under the writer
// lock so the "exactly one starred" invariant holds. No DELETE
// counterpart: the star moves only by starring another rig or by
// deleting the starred rig (auto-elects a successor server-side).
// Errors:
//   404 — rig not found (or soft-deleted)
//   200 — success; response body is the updated Rig
export async function starRig(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/rigs/${encodeURIComponent(id)}/star`,
    { method: 'PUT' },
  );
  return handle(res);
}


// POST /api/v1/rigs/{id}/swap_main (S.2)
// Swap the rig's current main canopy. The dedicated jumper-facing
// alternative to PUT (which forbids current_*_id changes per D37).
// Body: { new_main_id: UUID }. Returns the updated Rig. Errors:
//   404 — rig not found
//   422 — new main missing or non-active
//   409 — new main on a different rig (component_already_assigned)
//   200 — same id (no-op, rig returned unchanged)
export async function swapMain(rigId, newMainId) {
  const res = await fetch(
    `${API_BASE}/api/v1/rigs/${encodeURIComponent(rigId)}/swap_main`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_main_id: newMainId }),
    },
  );
  return handle(res);
}


// --------------------------------------------------------------------- //
// Inventory components (D33+D34, R.1) — mains / reserves / aads / containers
// --------------------------------------------------------------------- //
//
// Mirror the rigs/dropzones API client shape. Each kind has list +
// get; create/update/delete are deferred until the inventory views
// need them (the read-side wiring in Phase 1 only needs the lists).

export async function listMains({ limit = 100, offset = 0 } = {}) {
  const res = await fetch(
    `${API_BASE}/api/v1/mains?limit=${limit}&offset=${offset}`,
    noStoreInit,
  );
  return handle(res);
}

// GET /api/v1/mains/{id}
// Used by LogJumpModal's rig picker to display the canopy on the
// chosen rig (R.2.2-light.b) and by the rig-shape adapter.
export async function getMain(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/mains/${encodeURIComponent(id)}`,
    noStoreInit,
  );
  return handle(res);
}

export async function createMain(payload) {
  const res = await fetch(`${API_BASE}/api/v1/mains`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// PUT /api/v1/mains/{id}
// Full-replace per MainUpdate. Backend strips assigned_rig_id and
// preserves it from on-disk (D37 / R.2.0c.iii.b). A non-active
// status while the main is on a rig is rejected with 409.
export async function updateMain(id, payload) {
  const res = await fetch(`${API_BASE}/api/v1/mains/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// DELETE /api/v1/mains/{id}
// Soft-deletes to .trash/inventory/mains/. No cascade to rigs that
// reference the main — caller is responsible for warning the user.
export async function deleteMain(id) {
  const res = await fetch(`${API_BASE}/api/v1/mains/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  return handle(res);
}

export async function listReserves({ limit = 100, offset = 0 } = {}) {
  const res = await fetch(
    `${API_BASE}/api/v1/reserves?limit=${limit}&offset=${offset}`,
    noStoreInit,
  );
  return handle(res);
}

export async function getReserve(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/reserves/${encodeURIComponent(id)}`,
    noStoreInit,
  );
  return handle(res);
}

export async function createReserve(payload) {
  const res = await fetch(`${API_BASE}/api/v1/reserves`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function updateReserve(id, payload) {
  const res = await fetch(`${API_BASE}/api/v1/reserves/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function deleteReserve(id) {
  const res = await fetch(`${API_BASE}/api/v1/reserves/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  return handle(res);
}

export async function listAads({ limit = 100, offset = 0 } = {}) {
  const res = await fetch(
    `${API_BASE}/api/v1/aads?limit=${limit}&offset=${offset}`,
    noStoreInit,
  );
  return handle(res);
}

export async function getAad(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/aads/${encodeURIComponent(id)}`,
    noStoreInit,
  );
  return handle(res);
}

export async function createAad(payload) {
  const res = await fetch(`${API_BASE}/api/v1/aads`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function updateAad(id, payload) {
  const res = await fetch(`${API_BASE}/api/v1/aads/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function deleteAad(id) {
  const res = await fetch(`${API_BASE}/api/v1/aads/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  return handle(res);
}

export async function listContainers({ limit = 100, offset = 0 } = {}) {
  const res = await fetch(
    `${API_BASE}/api/v1/containers?limit=${limit}&offset=${offset}`,
    noStoreInit,
  );
  return handle(res);
}

export async function getContainer(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/containers/${encodeURIComponent(id)}`,
    noStoreInit,
  );
  return handle(res);
}

export async function createContainer(payload) {
  const res = await fetch(`${API_BASE}/api/v1/containers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function updateContainer(id, payload) {
  const res = await fetch(`${API_BASE}/api/v1/containers/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function deleteContainer(id) {
  const res = await fetch(`${API_BASE}/api/v1/containers/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  return handle(res);
}


// --------------------------------------------------------------------- //
// Jumpers (D33, R.2.0c.i)
// --------------------------------------------------------------------- //

export async function listJumpers({ limit = 100, offset = 0 } = {}) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers?limit=${limit}&offset=${offset}`,
    noStoreInit,
  );
  return handle(res);
}

export async function getJumper(id) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(id)}`,
    noStoreInit,
  );
  return handle(res);
}

// POST /api/v1/jumpers (R.2.0c.i)
// Body: { name?, exit_weight_lb, exit_weight_updated_at? }.
// v0.1 is single-jumper per D33; the UI calls this once at
// onboarding when listJumpers returns empty.
export async function createJumper(payload) {
  const res = await fetch(`${API_BASE}/api/v1/jumpers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}

// PUT /api/v1/jumpers/{id} (R.2.0c.i)
// Full-replace shape per JumpUpdate / DropzoneUpdate. Backend
// auto-bumps exit_weight_updated_at when exit_weight_lb changes
// from the on-disk value (D33 staleness clock reset).
export async function updateJumper(id, payload) {
  const res = await fetch(`${API_BASE}/api/v1/jumpers/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handle(res);
}


// --------------------------------------------------------------------- //
// Jumper attachments + credentials (D47, Phase F.A)
// --------------------------------------------------------------------- //
//
// Attachments and the five credential collections (memberships, cops,
// ratings, tandem-ratings, medicals) all live nested inside a single
// jumper document. Every CRUD op returns the FULL updated Jumper —
// the caller swaps the whole record into local state rather than
// merging per-collection. This matches the backend's "return updated"
// posture and avoids stale-collection bugs in the UI.

// POST /api/v1/jumpers/{id}/attachments  (D47, Phase C.4)
// Multipart with one ``file`` field. Server mints the attachment
// UUID and returns the full Jumper with the new attachment in its
// list.
export async function addJumperAttachment(jumperId, file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}/attachments`,
    { method: 'POST', body: fd },
  );
  return handle(res);
}

// DELETE /api/v1/jumpers/{id}/attachments/{attachment_id}  (D47, Phase C.4)
// Hard-deletes one attachment file + its <attachment> entry. Refuses
// (409) when any credential references the attachment via
// card_attachment_id — clear the reference first.
export async function deleteJumperAttachment(jumperId, attachmentId) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/attachments/${encodeURIComponent(attachmentId)}`,
    { method: 'DELETE' },
  );
  return handle(res);
}

// Memberships (D47, Phase D.1).
// POST appends; PUT full-replaces by id (id from URL path); DELETE
// removes. Body shape on POST/PUT is MembershipCreate (no id field).
export async function addJumperMembership(jumperId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}/memberships`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function updateJumperMembership(jumperId, membershipId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/memberships/${encodeURIComponent(membershipId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function deleteJumperMembership(jumperId, membershipId) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/memberships/${encodeURIComponent(membershipId)}`,
    { method: 'DELETE' },
  );
  return handle(res);
}

// CoPs (D47, Phase D.2).
export async function addJumperCop(jumperId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}/cops`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function updateJumperCop(jumperId, copId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/cops/${encodeURIComponent(copId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function deleteJumperCop(jumperId, copId) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/cops/${encodeURIComponent(copId)}`,
    { method: 'DELETE' },
  );
  return handle(res);
}

// Federation ratings (D47, Phase D.2).
export async function addJumperRating(jumperId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}/ratings`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function updateJumperRating(jumperId, ratingId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/ratings/${encodeURIComponent(ratingId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function deleteJumperRating(jumperId, ratingId) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/ratings/${encodeURIComponent(ratingId)}`,
    { method: 'DELETE' },
  );
  return handle(res);
}

// Tandem ratings — manufacturer-issued (D47, Phase D.2).
export async function addJumperTandemRating(jumperId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}/tandem-ratings`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function updateJumperTandemRating(jumperId, tandemRatingId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/tandem-ratings/${encodeURIComponent(tandemRatingId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function deleteJumperTandemRating(jumperId, tandemRatingId) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/tandem-ratings/${encodeURIComponent(tandemRatingId)}`,
    { method: 'DELETE' },
  );
  return handle(res);
}

// Medicals (D47, Phase D.2).
export async function addJumperMedical(jumperId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}/medicals`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function updateJumperMedical(jumperId, medicalId, payload) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/medicals/${encodeURIComponent(medicalId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return handle(res);
}

export async function deleteJumperMedical(jumperId, medicalId) {
  const res = await fetch(
    `${API_BASE}/api/v1/jumpers/${encodeURIComponent(jumperId)}` +
      `/medicals/${encodeURIComponent(medicalId)}`,
    { method: 'DELETE' },
  );
  return handle(res);
}


// --------------------------------------------------------------------- //
// Onboarding wizard (D65)
// --------------------------------------------------------------------- //
//
// The SPA reads `getOnboardingState` on every App mount to decide
// between (wizard) / (Profile resumption banner) / (no UI). The
// per-step forms reuse the existing entity endpoints above
// (createDropzone, createContainer, etc.); this module owns only
// the sentinel.

// GET /api/v1/onboarding
// Returns { completed, completed_at, status, has_jumper, has_dropzones, has_rigs }.
export async function getOnboardingState() {
  const res = await fetch(`${API_BASE}/api/v1/onboarding`, noStoreInit);
  return handle(res);
}

// POST /api/v1/onboarding/complete
// Body: { status: 'finished' | 'skipped' }. Stamps the sentinel and
// returns the updated state.
export async function completeOnboarding(status) {
  const res = await fetch(`${API_BASE}/api/v1/onboarding/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  return handle(res);
}

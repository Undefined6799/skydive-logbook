// Unit-of-measure preferences (D12).
//
// Storage on the wire is always meters (D12: "altitudes are stored
// in meters on the wire and in XML"). The UI converts at the edge
// based on a user preference persisted in localStorage. v0.1
// supports altitude-unit choice only — speed and weight are
// reserved seams (the Settings UI shows the toggles for those but
// they are not yet routed through anything).
//
// Default is feet (the dominant unit at most North-American DZs
// that this project's first user jumps at). Changing the unit in
// Settings dispatches a custom event so any mounted component
// using ``useAltitudeUnit`` re-renders without page reload.
//
// Conversion factor: 1 meter ≡ 3.280839895 ft (NIST). Rounding
// to the nearest whole foot/meter on display avoids fractional
// noise on jump altitudes (no one logs "13123.36 ft").

import { useEffect, useState } from 'react';

const ALTITUDE_KEY = 'skydiveLogbook.altitudeUnit';
const ALTITUDE_DEFAULT = 'ft';
const ALTITUDE_CHANGE_EVENT = 'skydive-altitude-unit-change';
const M_PER_FT = 0.3048;

export const ALTITUDE_OPTIONS = ['m', 'ft'];

export function getAltitudeUnit() {
  if (typeof window === 'undefined') return ALTITUDE_DEFAULT;
  return localStorage.getItem(ALTITUDE_KEY) || ALTITUDE_DEFAULT;
}

export function setAltitudeUnit(unit) {
  if (!ALTITUDE_OPTIONS.includes(unit)) return;
  localStorage.setItem(ALTITUDE_KEY, unit);
  // Custom event so other mounted components in the same tab
  // pick up the change. ``storage`` events only fire across tabs.
  window.dispatchEvent(
    new CustomEvent(ALTITUDE_CHANGE_EVENT, { detail: unit }),
  );
}

/**
 * React hook returning the current altitude unit and a setter.
 * Re-renders any component using it when the unit changes anywhere.
 */
export function useAltitudeUnit() {
  const [unit, setUnitState] = useState(getAltitudeUnit);
  useEffect(() => {
    function handleCustom(e) {
      setUnitState(e.detail);
    }
    function handleStorage(e) {
      if (e.key === ALTITUDE_KEY) {
        setUnitState(e.newValue || ALTITUDE_DEFAULT);
      }
    }
    window.addEventListener(ALTITUDE_CHANGE_EVENT, handleCustom);
    window.addEventListener('storage', handleStorage);
    return () => {
      window.removeEventListener(ALTITUDE_CHANGE_EVENT, handleCustom);
      window.removeEventListener('storage', handleStorage);
    };
  }, []);
  return [unit, setAltitudeUnit];
}

/**
 * Convert meters → display value rounded to the user's selected unit.
 * Returns '' for null/undefined/empty input so it round-trips through
 * a controlled input cleanly.
 */
export function metersToDisplay(meters, unit = getAltitudeUnit()) {
  if (meters === null || meters === undefined || meters === '') return '';
  const m = Number(meters);
  if (!Number.isFinite(m)) return '';
  if (unit === 'ft') return Math.round(m / M_PER_FT);
  return Math.round(m);
}

/**
 * Convert a display value (in the user's selected unit) → meters.
 * Returns ``null`` for empty input so the caller can pass it as the
 * ``null`` payload for an optional altitude.
 *
 * Returns a FLOAT, not an int. The wire format is ``xs:decimal``
 * (per the SCHEMA.v1.xsd amend that lifted altitudes from
 * ``xs:nonNegativeInteger``) precisely so unit conversion can
 * round-trip cleanly: 13500 ft → 4114.8 m → 13500 ft. Storing
 * 4115 m (integer rounded) would re-display as 13501.
 */
export function displayToMeters(display, unit = getAltitudeUnit()) {
  if (display === null || display === undefined || display === '') return null;
  const v = Number(display);
  if (!Number.isFinite(v)) return null;
  if (unit === 'ft') return v * M_PER_FT;
  return v;
}

/** Suffix for the altitude unit (e.g. "FT", "M"). */
export function altitudeSuffix(unit = getAltitudeUnit()) {
  return unit.toUpperCase();
}

/**
 * Sensible default exit altitudes for new jumps, in the display
 * unit. 13500 ft / 4000 m matches a typical full-altitude jump
 * pass; 3500 ft / 1100 m is the sport-jumper deployment default
 * (USPA SIM recommends ≥3500 ft for licensed jumpers).
 */
export function defaultDisplayExitAltitude(unit = getAltitudeUnit()) {
  return unit === 'ft' ? '13500' : '4000';
}

export function defaultDisplayDeploymentAltitude(unit = getAltitudeUnit()) {
  return unit === 'ft' ? '3500' : '1100';
}

// Suspension-line taxonomy. Each material is its own family of
// line; the variants are the strength grades the user can pick.
// The number in each variant is the manufacturer's published
// breaking strength in lb — D45's wear math reads from that, so
// the modal forms auto-fill BREAKING STRENGTH when a variant is
// picked.
//
// Vectran (LCP, liquid-crystal polymer): low creep, ZP-friendly,
//   the V-series. Numbers are breaking strength lb (V750 ≈ 750 lb).
// HMA (high-modulus aramid, Technora-based): heat-tolerant, long
//   fatigue life. Sub-types named by breaking strength in lb.
// Spectra / Microline (UHMWPE): heat-shrinks, common on legacy
//   mains. Sub-types named by breaking strength in lb.
// Dacron (polyester): thick, stretchy, used on student canopies.
// Other: free-form so the user can record anything outside the
//   common four (Technora-only, custom rigger lines, etc.).
export const LINE_MATERIALS = {
  vectran: {
    label: 'Vectran',
    variants: [
      { value: 'V300', strength: 300 },
      { value: 'V400', strength: 400 },
      { value: 'V500', strength: 500 },
      { value: 'V550', strength: 550 },
      { value: 'V650', strength: 650 },
      { value: 'V725', strength: 725 },
      { value: 'V750', strength: 750 },
    ],
  },
  hma: {
    label: 'HMA',
    variants: [
      { value: '500', strength: 500 },
      { value: '600', strength: 600 },
      { value: '700', strength: 700 },
      { value: '825', strength: 825 },
      { value: '1000', strength: 1000 },
    ],
  },
  spectra: {
    label: 'Spectra / Microline',
    variants: [
      { value: '500', strength: 500 },
      { value: '750', strength: 750 },
      { value: '825', strength: 825 },
    ],
  },
  dacron: {
    label: 'Dacron',
    variants: [
      { value: '500', strength: 500 },
      { value: '750', strength: 750 },
    ],
  },
  other: {
    label: 'Other / Custom',
    variants: [],
  },
};


// Combine material + variant into the canonical line_type string
// the backend stores. Vectran variants already include a "V" prefix
// so we still write it as "Vectran V750" — explicit material in the
// file beats clever shorthand. HMA/Spectra/Dacron get "<Label> <N>".
// "Other" is free-form so we trust the user's variant string verbatim.
export function composeLineType(material, variant) {
  if (!material || !variant) return '';
  if (material === 'other') return variant.trim();
  const label = LINE_MATERIALS[material]?.label;
  if (!label) return variant.trim();
  return `${label} ${variant}`.trim();
}


// Inverse of composeLineType: given the canonical "Material Variant"
// string the backend returns, recover (material, variant) so the
// edit form can populate its dropdowns with the right initial state.
// Returns { material: '', variant: '' } when the string doesn't
// match any known material — caller should fall back to "other"
// with the raw string in variant.
export function decomposeLineType(lineType) {
  if (!lineType) return { material: '', variant: '' };
  const trimmed = lineType.trim();
  for (const [key, m] of Object.entries(LINE_MATERIALS)) {
    if (key === 'other') continue;
    const prefix = `${m.label} `;
    if (trimmed.startsWith(prefix)) {
      const variant = trimmed.slice(prefix.length).trim();
      // Confirm the variant is one we know about — otherwise the
      // user wrote something unusual and we should land in "other"
      // so the form doesn't pretend the value is canonical.
      const known = m.variants.find((v) => v.value === variant);
      if (known) return { material: key, variant };
    }
  }
  return { material: 'other', variant: trimmed };
}

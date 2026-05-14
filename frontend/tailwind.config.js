/** @type {import('tailwindcss').Config} */
//
// The neutral ramp is overridden to point at the design-system CSS
// variables declared in src/index.css. That way every existing
// `text-neutral-500` / `bg-neutral-900` / etc. resolves through the
// same tokens as `style={{ color: 'var(--text-muted)' }}` — keeping
// the palette consistent without touching every JSX file. Tailwind's
// 50-950 stops are kept; we map them onto the closest token so the
// design system remains the single source of truth.
//
// 50 / 100 / 200      → --text          (primary text)
// 300 / 400           → --text-muted    (secondary text)
// 500 / 600           → --text-faint    (placeholder, footer)
// 700                 → --surface-3     (active row, input fill)
// 800                 → --surface-2     (hover surface, button bg)
// 900                 → --surface-1     (card surface)
// 950                 → --bg            (app background)
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Archivo', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      colors: {
        neutral: {
          50:  'var(--text)',
          100: 'var(--text)',
          200: 'var(--text)',
          300: 'var(--text-muted)',
          400: 'var(--text-muted)',
          500: 'var(--text-faint)',
          600: 'var(--text-faint)',
          700: 'var(--surface-3)',
          800: 'var(--surface-2)',
          900: 'var(--surface-1)',
          950: 'var(--bg)',
        },
        // Status colors mapped to design-system pastels so any
        // `text-emerald-*` / `text-amber-*` / `text-red-*` left in
        // legacy code still renders in the right key. We override
        // only the most-used stops; less-common Tailwind ramps stay
        // at their stock values.
        emerald: {
          300: 'var(--status-ready)',
          400: 'var(--status-ready)',
          500: 'var(--status-ready)',
          600: 'var(--status-ready)',
        },
        amber: {
          300: 'var(--status-watch)',
          400: 'var(--status-watch)',
          500: 'var(--status-watch)',
          600: 'var(--status-watch)',
        },
        red: {
          300: 'var(--status-critical)',
          400: 'var(--status-critical)',
          500: 'var(--status-critical)',
          600: 'var(--status-critical)',
        },
      },
    },
  },
  plugins: [],
};

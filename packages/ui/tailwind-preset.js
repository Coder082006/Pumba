/**
 * Shared Tailwind preset — the single source of design tokens for the tourist
 * site and the console (brief: "Same Tailwind + shadcn/ui design tokens as the
 * tourist site, shared via packages/ui").
 *
 * Colours are declared as CSS custom properties in HSL channel form so that
 * shadcn/ui primitives, which expect `hsl(var(--token))`, work unmodified and
 * a theme can be swapped without rebuilding.
 */

/** @type {Partial<import('tailwindcss').Config>} */
export default {
  darkMode: ['class'],
  theme: {
    container: {
      center: true,
      padding: '1rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
          // Dark enough to set text on a light surface. `accent` itself is
          // 2.9:1 on the background and fails as an ink — a failure invisible
          // to anyone with good eyes and a good screen.
          ink: 'hsl(var(--accent-ink))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
          // For inline error text on the page background, where the filled
          // banner colour is too light to read.
          ink: 'hsl(var(--destructive-ink))',
        },
        // A distinct semantic from `accent`. A "this stay has no location"
        // notice and a "book this" button must not be able to converge.
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          foreground: 'hsl(var(--warning-foreground))',
          border: 'hsl(var(--warning-border))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        // Headings and pull quotes. Paired with `sans` for body text, which
        // is the editorial arrangement a travel site wants: character in the
        // headline, legibility in the paragraph.
        display: ['var(--font-display)', 'Georgia', 'serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'monospace'],
      },
      // Both are tokens so `prefers-reduced-motion` can flatten every
      // animation from one place in `globals.css`. A component reaching for
      // `duration-300` opts itself out of that guarantee without saying so.
      transitionDuration: {
        fast: 'var(--duration-fast)',
        base: 'var(--duration-base)',
        slow: 'var(--duration-slow)',
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
        'in-out': 'var(--ease-in-out)',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
      },
      // A tourism site leads with photographs, and a hero needs to be able to
      // say how tall it is without a magic number in a page file.
      height: {
        hero: 'clamp(22rem, 62vh, 40rem)',
        'hero-sm': 'clamp(16rem, 42vh, 26rem)',
      },
    },
  },
  plugins: [],
};

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Deep navy/black field the glass cards float on.
        void: '#05070F',
        abyss: '#080B18',
        surface: '#0B1020',
        // Stage / state accents. Each carries meaning, not decoration:
        // cyan = the agent, violet = policy & memory, emerald = admissible,
        // amber = human-in-loop, rose = blocked or escalated.
        agent: '#22D3EE',
        policy: '#8B5CF6',
        pass: '#34D399',
        hold: '#FBBF24',
        block: '#FB7185',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      boxShadow: {
        glass: '0 1px 0 0 rgba(255,255,255,0.05) inset, 0 20px 50px -25px rgba(0,0,0,0.9)',
        glow: '0 0 30px -6px rgba(34,211,238,0.45)',
      },
      keyframes: {
        corepulse: {
          '0%, 100%': { opacity: '0.55', transform: 'scale(1)' },
          '50%': { opacity: '1', transform: 'scale(1.04)' },
        },
        sweep: {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
        drift: {
          '0%': { strokeDashoffset: '0' },
          '100%': { strokeDashoffset: '-40' },
        },
        risein: {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        corepulse: 'corepulse 2.8s ease-in-out infinite',
        sweep: 'sweep 9s linear infinite',
        drift: 'drift 1.6s linear infinite',
        risein: 'risein 0.25s ease-out both',
      },
    },
  },
  plugins: [],
}

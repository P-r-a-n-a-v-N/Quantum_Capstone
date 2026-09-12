/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // A cold, instrument-panel palette: near-black slate carrying cyan for
        // live signal and amber for degraded state.
        lab: {
          950: '#05070d',
          900: '#0a0e1a',
          850: '#0f1524',
          800: '#141c2e',
          700: '#1e293f',
          600: '#2c3a55',
        },
        signal: {
          cyan: '#22d3ee',
          violet: '#a78bfa',
          amber: '#fbbf24',
          green: '#34d399',
          rose: '#fb7185',
        },
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      keyframes: {
        'pulse-dot': {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.45', transform: 'scale(0.82)' },
        },
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(-4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'pulse-dot': 'pulse-dot 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fade-in 0.25s ease-out',
      },
    },
  },
  plugins: [],
}

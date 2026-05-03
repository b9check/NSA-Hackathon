/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg:      '#0A0E14',
        panel:   '#141B26',
        panel2:  '#1B2433',
        line:    '#243044',
        fg:      '#E6EAF2',
        mute:    '#7A879A',
        blue:    '#4DA3FF',
        bluedim: '#2C6FB3',
        red:     '#FF4D5E',
        reddim:  '#B33745',
        amber:   '#FFB84D',
        green:   '#4DD18F',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f0f9ff',
          500: '#0EA5E9',
          600: '#0284c7',
          700: '#0369a1',
        }
      }
    }
  },
  plugins: []
}

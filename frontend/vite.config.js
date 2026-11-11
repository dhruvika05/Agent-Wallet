import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'


// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],   // keep whatever the template already has here
  server: {
    proxy: { "/v1": "http://localhost:8000" },
  },
});



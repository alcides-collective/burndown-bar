import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' so the built assets resolve no matter what path the stdlib server
// mounts them under.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: 'dist', emptyOutDir: true },
})

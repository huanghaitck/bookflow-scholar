import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  publicDir: 'design/mascots',
  server: {
    host: '127.0.0.1',
    port: 4317,
    watch: { ignored: ['**/src-tauri/**', '**/target/**'] },
  },
  build: {
    rollupOptions: {
      input: {
        app: new URL('./index.html', import.meta.url).pathname,
        mascotRuntime: new URL('./mascot-runtime.html', import.meta.url).pathname,
      },
    },
  },
});

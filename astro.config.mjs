import { defineConfig } from 'astro/config';
import svelte from '@astrojs/svelte';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  // The Quodlibet page moved when it was renamed to halo-halo tab. Anyone who
  // bookmarked it, or any link written down before today, still has the old address —
  // and a download page that 404s is a download page nobody reaches.
  redirects: {
    '/admin/quodlibet': '/admin/halo-halo-tab',
  },
  integrations: [svelte()],
  server: { port: 4321 },
  vite: {
    plugins: [tailwindcss()],
    server: {
      proxy: {
        '/api': 'http://localhost:5000',
      },
    },
  },
});

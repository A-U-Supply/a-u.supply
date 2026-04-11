import { defineConfig } from 'astro/config';

export default defineConfig({
  server: { port: 4321 },
  vite: {
    server: {
      proxy: {
        '/register': 'http://localhost:5000',
        '/login': 'http://localhost:5000',
        '/me': 'http://localhost:5000',
      },
    },
  },
});

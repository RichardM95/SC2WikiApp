import {defineConfig, loadEnv, type ProxyOptions} from 'vite';

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, process.cwd(), 'SCORE_');
  const target = new URL(env.SCORE_BACKEND_URL || 'http://127.0.0.1:18765');
  if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1' ||
      target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
    throw new Error('SCORE_BACKEND_URL 必须是 http://127.0.0.1:端口');
  }
  const backend = target.origin;
  const proxy: ProxyOptions = {
    target: backend,
    changeOrigin: true,
    configure(server) {
      server.on('proxyReq', (outgoing, incoming) => {
        // Only rewrite same-origin development UI requests; do not relax Go authorization.
        if (incoming.headers.origin === `http://${incoming.headers.host}`) {
          outgoing.setHeader('Origin', backend);
        }
      });
    },
  };
  return {
    base: './',
    build: {target: 'es2022'},
    server: {
      host: '127.0.0.1', port: 5173, strictPort: true,
      proxy: {'/__app': proxy, '/__content': proxy, '/manifest.json': proxy},
    },
  };
});

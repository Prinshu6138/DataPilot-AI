import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
export default defineConfig({
 plugins:[react(),tailwindcss()],
 server:{host:'0.0.0.0',allowedHosts:true,proxy:{'/api':{target:'http://127.0.0.1:8000',changeOrigin:false}}},
 build:{chunkSizeWarningLimit:540,rollupOptions:{output:{manualChunks(id){if(id.includes('/node_modules/')){if(/\/(recharts|d3-[^/]+|victory-vendor|decimal.js-light|es-toolkit|immer|react-redux|redux|@reduxjs)\//.test(id))return 'charts';if(/\/(react|react-dom|scheduler)\//.test(id))return 'react';}}}}}
});

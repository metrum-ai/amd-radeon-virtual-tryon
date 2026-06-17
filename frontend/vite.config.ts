// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Dev-only proxy; production routes through nginx.
    proxy: {
      '/pipeline': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/pipeline/, ''),
      },
    },
  },
})

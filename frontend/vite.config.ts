import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import vue from '@vitejs/plugin-vue'

const frontendRoot = fileURLToPath(new URL('.', import.meta.url))
const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8')) as {
  version: string
}
const buildTime = new Date().toISOString()

function versionJsonPlugin() {
  return {
    name: 'frontend-version-json',
    closeBundle() {
      const dist = resolve(frontendRoot, 'dist')
      mkdirSync(dist, { recursive: true })
      writeFileSync(
        resolve(dist, 'version.json'),
        `${JSON.stringify({ frontend_version: pkg.version, build_time: buildTime }, null, 2)}\n`,
      )
    },
  }
}

// 可配置部署基址：路径别名部署时设 VITE_BASE=/home-bookshelf/
// hostPort 直连或后端直接托管时保持默认 '/'
const base = process.env.VITE_BASE || '/'

export default defineConfig({
  base,
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  plugins: [vue(), versionJsonPlugin()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})

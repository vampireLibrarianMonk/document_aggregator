// Shared static-file server + /api proxy, used by BOTH the a11y runner
// (a11y/run.mjs) and the Playwright E2E suite (playwright.config.ts webServer).
//
// It serves the production build in `distDir` with SPA fallback, and proxies
// `apiPrefix` -> `apiTarget` so data-dependent views render against the live
// backend. Extracted so the two test surfaces share one server implementation.

import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { join, extname } from 'node:path'
import { pathToFileURL } from 'node:url'

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
  '.woff': 'font/woff', '.woff2': 'font/woff2',
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => resolve(Buffer.concat(chunks)))
  })
}

/**
 * Create (but do not start) the static+proxy HTTP server.
 * @param {object} opts
 * @param {string} opts.distDir   absolute or cwd-relative path to the build dir
 * @param {string} opts.apiPrefix e.g. '/api'
 * @param {string} opts.apiTarget e.g. 'http://127.0.0.1:8000'
 * @param {(up: boolean) => void} [opts.onBackend] called true/false per /api hit
 */
export function createAppServer({ distDir, apiPrefix, apiTarget, onBackend }) {
  const DIST = join(process.cwd(), distDir)
  return createServer(async (req, res) => {
    const url = req.url || '/'
    if (url.startsWith(apiPrefix + '/')) {
      try {
        const upstream = await fetch(apiTarget + url.slice(apiPrefix.length), {
          method: req.method,
          headers: { 'content-type': req.headers['content-type'] || 'application/json' },
          body: ['GET', 'HEAD'].includes(req.method) ? undefined : await readBody(req),
        })
        onBackend?.(true)
        const buf = Buffer.from(await upstream.arrayBuffer())
        res.writeHead(upstream.status, {
          'Content-Type': upstream.headers.get('content-type') || 'application/json',
        })
        res.end(buf)
      } catch {
        onBackend?.(false)
        res.writeHead(502)
        res.end('{"detail":"backend unreachable"}')
      }
      return
    }
    try {
      const urlPath = decodeURIComponent(url.split('?')[0])
      let filePath = join(DIST, urlPath === '/' ? 'index.html' : urlPath)
      let body
      try {
        body = await readFile(filePath)
      } catch {
        filePath = join(DIST, 'index.html') // SPA fallback
        body = await readFile(filePath)
      }
      res.writeHead(200, { 'Content-Type': MIME[extname(filePath)] || 'application/octet-stream' })
      res.end(body)
    } catch (e) {
      res.writeHead(500)
      res.end(String(e))
    }
  })
}

// CLI mode: `node a11y/serve.mjs [port]` — used as the Playwright webServer.
// Config via env so playwright.config.ts can point it at the right dist/port/api.
if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.E2E_PORT || process.argv[2] || 4600)
  const server = createAppServer({
    distDir: process.env.E2E_DIST || 'dist',
    apiPrefix: process.env.E2E_API_PREFIX || '/api',
    apiTarget: process.env.E2E_API_TARGET || 'http://127.0.0.1:8000',
  })
  server.listen(port, '127.0.0.1', () => {
    // eslint-disable-next-line no-console
    console.log(`[e2e-serve] serving ${process.env.E2E_DIST || 'dist'} on http://127.0.0.1:${port} (/api -> ${process.env.E2E_API_TARGET || 'http://127.0.0.1:8000'})`)
  })
}

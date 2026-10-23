// Vite config: dev/preview-only middleware that converts an uploaded .dem
// into a replay.bin using the local Rust dumper. The production bundle stays
// a static site; this endpoint only exists while `npm run dev` /
// `npm run preview` is running, on the same origin (no CORS involved).
//
// POST /__dump_demo
//   body: raw .dem bytes
//   200 -> application/octet-stream, body = replay.bin (self-contained:
//          meta.json is embedded in the format, see web/src/replay.ts)
//   4xx/5xx -> application/json { error: string, map?: string }
//
// Only pl_upward is accepted: the viewer currently hardcodes the upward map
// GLB, so any other demo would render on the wrong geometry.
import { defineConfig, type Connect, type Plugin } from 'vite';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import * as fsp from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// 1 GiB cap on uploaded demo bodies (a POV demo is tens to hundreds of MB).
const MAX_DEMO_BYTES = 1_073_741_824;
const SUPPORTED_MAP = 'pl_upward';

const REPO_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
);

/** Locate the dumper binary; built with `cargo build --release`. */
function dumperPath(): string | null {
  const base = path.join(REPO_ROOT, 'preprocess', 'target', 'release');
  for (const name of ['dem2replay', 'dem2replay.exe']) {
    const full = path.join(base, name);
    if (existsSync(full)) return full;
  }
  return null;
}

/** Read the full request body, rejecting uploads past the size cap. */
async function readBody(req: Connect.IncomingMessage): Promise<Buffer | null> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    const buf = chunk as Buffer;
    size += buf.length;
    if (size > MAX_DEMO_BYTES) return null;
    chunks.push(buf);
  }
  return Buffer.concat(chunks);
}

/** JSON error response helper. */
function sendJson(res: Connect.ServerResponse, status: number, payload: object): void {
  const body = Buffer.from(JSON.stringify(payload), 'utf-8');
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': body.length,
    'cache-control': 'no-store',
  });
  res.end(body);
}

async function handleDemoDump(
  req: Connect.IncomingMessage,
  res: Connect.ServerResponse,
): Promise<void> {
  const dumper = dumperPath();
  if (!dumper) {
    sendJson(res, 503, {
      error: 'dem2replay is not built; run: cd preprocess && cargo build --release',
    });
    return;
  }

  const body = await readBody(req);
  if (body === null) {
    sendJson(res, 413, { error: 'demo file too large' });
    return;
  }
  if (body.length === 0) {
    sendJson(res, 400, { error: 'empty request body' });
    return;
  }

  const dir = await fsp.mkdtemp(path.join(tmpdir(), 'dem2replay-'));
  try {
    const demoPath = path.join(dir, 'demo.dem');
    const outDir = path.join(dir, 'out');
    await fsp.writeFile(demoPath, body);
    await fsp.mkdir(outDir, { recursive: true });
    const result = await new Promise<{ code: number; stderr: string }>((resolve) => {
      const child = spawn(dumper, [demoPath, outDir], { stdio: ['ignore', 'ignore', 'pipe'] });
      let stderr = '';
      child.stderr.on('data', (chunk) => { stderr += String(chunk); });
      child.on('error', (err) => resolve({ code: -1, stderr: String(err) }));
      child.on('close', (code) => resolve({ code: code ?? -1, stderr }));
    });
    if (result.code !== 0) {
      const tail = result.stderr.trim().split('\n').slice(-3).join('\n');
      sendJson(res, 500, { error: `dem2replay failed (code ${result.code}): ${tail}` });
      return;
    }

    // The dumper writes replay.bin + meta.json; meta travels inside the bin
    // too, but the map gate is cheaper to enforce here from meta.json.
    let meta: { map?: string };
    try {
      meta = JSON.parse(await fsp.readFile(path.join(outDir, 'meta.json'), 'utf-8'));
    } catch {
      sendJson(res, 500, { error: 'dumper produced no readable meta.json' });
      return;
    }
    const map = String(meta.map ?? '').toLowerCase();
    if (map !== SUPPORTED_MAP) {
      sendJson(res, 415, { error: `unsupported map "${meta.map}"`, map: meta.map ?? '' });
      return;
    }

    const bin = await fsp.readFile(path.join(outDir, 'replay.bin'));
    res.writeHead(200, {
      'content-type': 'application/octet-stream',
      'content-length': bin.length,
      'cache-control': 'no-store',
      'x-demo-map': map,
    });
    res.end(bin);
  } finally {
    await fsp.rm(dir, { recursive: true, force: true });
  }
}

function noStorePublicAssets(): Plugin {
  // Dev/preview only: public assets (map GLB, lightmap atlas, prop GLBs,
  // replay.bin) are rebuilt in place by the python chain while the server
  // runs. Firefox kept serving a stale pl_upward.glb from its disk cache
  // (the file has no built-in revalidation trigger), which made every
  // pipeline fix invisible until a manual cache wipe. Serve them no-store
  // so a plain reload always shows the latest build.
  const mount = (server: { middlewares: Connect.Server }): void => {
    server.middlewares.use((req, res, next) => {
      const url = req.url?.split('?')[0] ?? '';
      if (url.startsWith('/maps/') || url.startsWith('/models/')
          || url.startsWith('/replay/')) {
        res.setHeader('cache-control', 'no-store');
      }
      next();
    });
  };
  return {
    name: 'no-store-public-assets',
    configureServer: mount,
    configurePreviewServer: mount,
  };
}

function demoDumperPlugin(): Plugin {
  const mount = (server: { middlewares: Connect.Server }): void => {
    server.middlewares.use((req, res, next) => {
      if (req.method !== 'POST' || req.url?.split('?')[0] !== '/__dump_demo') {
        next();
        return;
      }
      handleDemoDump(req, res).catch((err: unknown) => {
        if (!res.headersSent) {
          sendJson(res, 500, { error: `internal error: ${String(err)}` });
        } else {
          res.destroy();
        }
      });
    });
  };
  return {
    name: 'dump-demo',
    configureServer: mount,
    configurePreviewServer: mount,
  };
}

export default defineConfig({
  plugins: [noStorePublicAssets(), demoDumperPlugin()],
});

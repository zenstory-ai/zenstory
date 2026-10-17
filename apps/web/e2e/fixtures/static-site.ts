import { test as base, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { extname, join, resolve } from 'node:path';

// These pages are generated HTML, not Vite's development SPA. Build one isolated
// copy per worker and serve it through the browser fixture in local and CI runs.
const webRoot = resolve(import.meta.dirname, '../..');
const sharedPrefixes = (JSON.parse(readFileSync(join(webRoot, 'content/site-routing.json'), 'utf8')) as { sharedPrefixes: string[] }).sharedPrefixes;
const types: Record<string, string> = {
  '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.json': 'application/json',
};
export const test = base.extend<{ staticSite: void }, { staticDir: string }>({
  staticDir: [async ({ browserName }, provide) => {
    void browserName;
    const dir = mkdtempSync(join(tmpdir(), 'zenstory-static-browser-'));
    try {
      for (const script of ['build-org-pages.mjs', 'build-docs-pages.mjs']) {
        execFileSync(process.execPath, [join(webRoot, 'scripts', script), dir], { cwd: webRoot });
      }
      writeFileSync(join(dir, 'index.html'), readFileSync(join(webRoot, 'index.html')));
      execFileSync(process.execPath, [join(webRoot, 'scripts/build-site-layout.mjs'), dir], { cwd: webRoot });
      await provide(dir);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }, { scope: 'worker' }],
  staticSite: [async ({ page, baseURL, staticDir }, use) => {
    const origin = new URL(baseURL!).origin;
    await page.route(`${origin}/**`, async route => {
      const pathname = new URL(route.request().url()).pathname;
      const rootPrefix = pathname.split('/')[1];
      let file = join(staticDir, sharedPrefixes.includes(rootPrefix) ? '_site' : '', pathname);
      if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
      if (!existsSync(file)) file = join(webRoot, 'public', pathname);
      if (!existsSync(file) || statSync(file).isDirectory()) {
        await route.fulfill({ status: 404, body: 'Not found' });
        return;
      }
      await route.fulfill({ contentType: types[extname(file)] ?? 'application/octet-stream', body: readFileSync(file) });
    });
    await use();
  }, { auto: true }],
});
export { expect };

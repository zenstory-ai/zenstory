import { test as base, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { extname, join, resolve, sep } from 'node:path';

const webRoot = resolve(import.meta.dirname, '..');
const mime: Record<string, string> = {
  '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.woff2': 'font/woff2', '.ico': 'image/x-icon',
};

const outputState = (path: string) => {
  if (!existsSync(path)) return null;
  const stat = statSync(path);
  return { size: stat.size, mtimeMs: stat.mtimeMs };
};

// Helmet's dev StrictMode lifecycle differs from production. This regression
// exercises the actual production React bundle, not jsdom or the Vite dev shell.
const test = base.extend<object, { appBuild: string }>({
  appBuild: [async ({ browserName }, provide) => {
    void browserName;
    const workspace = mkdtempSync(join(tmpdir(), 'zenstory-app-legal-'));
    const productionOutputs = [join(webRoot, 'dist', 'sitemap.xml'), join(webRoot, 'stats.html')];
    const initialOutputState = productionOutputs.map(outputState);
    try {
      for (const file of ['index.html', 'package.json', 'postcss.config.js', 'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json', 'vite.config.ts']) {
        copyFileSync(join(webRoot, file), join(workspace, file));
      }
      for (const dir of ['content', 'docs', 'node_modules', 'public', 'src']) {
        symlinkSync(join(webRoot, dir), join(workspace, dir), 'dir');
      }
      execFileSync(process.execPath, [join(webRoot, 'node_modules/vite/bin/vite.js'), 'build'], {
        cwd: workspace, env: { ...process.env, NODE_ENV: 'production' }, stdio: 'pipe', timeout: 120_000,
      });
      assert.ok(existsSync(join(workspace, 'dist', 'sitemap.xml')), 'the production sitemap plugin must run in the isolated workspace');
      assert.ok(existsSync(join(workspace, 'stats.html')), 'the production bundle visualizer must run in the isolated workspace');
      assert.deepEqual(productionOutputs.map(outputState), initialOutputState, 'isolated legal fixture must not touch the real web build outputs');
      await provide(join(workspace, 'dist'));
    } finally {
      rmSync(workspace, { recursive: true, force: true });
    }
  }, { scope: 'worker', timeout: 150_000 }],
});

for (const host of ['app.zenstory.ai', 'app-preview.zenstory.ai']) {
  for (const language of ['zh', 'en']) {
    for (const path of ['/privacy-policy', '/terms-of-service']) {
      test(`built legal reader retains metadata ${host} ${language} ${path}`, async ({ page, appBuild }, info) => {
        const origin = `https://${host}`;
        const errors: string[] = [];
        const unexpected: string[] = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.setViewportSize({ width: 1440, height: 1000 });
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await page.addInitScript(language => localStorage.setItem('zenstory-language', language), language);
        await page.route('**/*', route => {
          const request = route.request();
          const url = new URL(request.url());
          // Deliberately offline: host identity is real in the browser, bytes are
          // the local production build. This does not certify a hosted deployment.
          if (url.origin !== origin) return route.abort();
          const file = request.resourceType() === 'document'
            ? join(appBuild, 'index.html')
            : resolve(appBuild, '.' + url.pathname);
          if (!file.startsWith(appBuild + sep) || !existsSync(file) || !statSync(file).isFile()) {
            unexpected.push(`${request.method()} ${url.pathname}`);
            return route.fulfill({ status: 404, body: 'Offline fixture: no matching asset' });
          }
          return route.fulfill({ contentType: mime[extname(file)] ?? 'application/octet-stream', body: readFileSync(file) });
        });
        const title = path === '/privacy-policy'
          ? language === 'zh' ? '隐私政策' : 'Privacy Policy'
          : language === 'zh' ? '服务条款' : 'Terms of Service';
        const assertReader = async () => {
          await expect(page).toHaveURL(origin + path);
          await expect(page.locator('#root main h1')).toHaveText(title);
          await expect(page).toHaveTitle(`${title} - ZenStory`);
          await expect(page.locator('link[rel="canonical"]')).toHaveCount(1);
          await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', 'https://zenstory.ai' + path);
          await expect(page.locator('meta[property="og:url"]')).toHaveAttribute('content', 'https://zenstory.ai' + path);
          await expect(page.locator('meta[property="og:title"]')).toHaveAttribute('content', `${title} - ZenStory`);
          await expect(page.locator('#legal-zh')).toHaveCount(0);
        };
        await page.goto(origin + '/');
        await page.locator(`footer a[href="${path}"]`).click();
        await assertReader();
        await page.reload();
        await assertReader();
        await page.screenshot({ path: info.outputPath('built-app-legal.png'), animations: 'disabled' });
        expect(unexpected, 'No API, generation, payment or missing local asset is needed').toEqual([]);
        expect(errors).toEqual([]);
      });
    }
  }
}

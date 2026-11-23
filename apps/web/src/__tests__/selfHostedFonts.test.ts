import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const GOOGLE_FONTS_HOST_RE = /fonts\.googleapis\.com|fonts\.gstatic\.com/i;

function extractFontFaceBlocks(css: string): string[] {
  return [...css.matchAll(/@font-face\s*\{[\s\S]*?\}/g)].map((m) => m[0]);
}

function extractLocalFontUrls(css: string): string[] {
  return [...css.matchAll(/url\(["']?(\/fonts\/[^)"']+\.woff2)["']?\)/g)].map((m) => m[1]);
}

describe('self-hosted fonts contract', () => {
  it('contains zero Google Fonts references across app HTML, org shell, and stylesheets', () => {
    const files = [
      'index.html',
      'src/index.css',
      'scripts/site-shell.mjs',
      'scripts/org-pages.css',
      'scripts/build-org-pages.mjs',
      'scripts/build-docs-pages.mjs',
    ];

    for (const file of files) {
      const content = readFileSync(file, 'utf8');
      expect(content, `${file} must not reference Google Fonts`).not.toMatch(GOOGLE_FONTS_HOST_RE);
    }
  });

  it('ships a valid WOFF2 file on disk with font-display: swap for every local /fonts/*.woff2 URL in CSS', () => {
    const stylesheets = ['src/index.css', 'scripts/org-pages.css'];
    const allFontUrls = new Set<string>();

    for (const stylesheet of stylesheets) {
      const css = readFileSync(stylesheet, 'utf8');
      const blocks = extractFontFaceBlocks(css);
      expect(blocks.length, `${stylesheet} should declare @font-face rules`).toBeGreaterThan(0);

      for (const block of blocks) {
        expect(block, `${stylesheet} @font-face must set font-display: swap`).toMatch(
          /font-display:\s*swap\s*;/,
        );
        const urls = extractLocalFontUrls(block);
        expect(urls.length, `${stylesheet} @font-face must reference a local /fonts/*.woff2`).toBeGreaterThan(0);
        for (const url of urls) {
          allFontUrls.add(url);
        }
      }
    }

    expect(allFontUrls.size).toBeGreaterThanOrEqual(6);

    for (const url of allFontUrls) {
      const diskPath = join('public', url.replace(/^\//, ''));
      expect(existsSync(diskPath), `Missing self-hosted font file: ${diskPath}`).toBe(true);
      const bytes = readFileSync(diskPath);
      expect(bytes.length, `Empty font file: ${diskPath}`).toBeGreaterThan(1024);
      expect(bytes.subarray(0, 4).toString('ascii'), `${diskPath} must be a valid WOFF2 binary`).toBe('wOF2');
    }
  });

  it('keeps expected primary Latin/Mono families and native CJK system fallbacks in workbench CSS tokens', () => {
    const css = readFileSync('src/index.css', 'utf8');

    expect(css).toMatch(/--font-display:\s*"Crimson Pro"[^;]*Songti SC[^;]*serif;/);
    expect(css).toMatch(/--font-body:\s*"Source Serif 4"[^;]*Songti SC[^;]*serif;/);
    expect(css).toMatch(/--font-sans:\s*"Plus Jakarta Sans"[^;]*PingFang SC[^;]*sans-serif;/);
    expect(css).toMatch(/--font-mono:\s*"JetBrains Mono"[^;]*monospace;/);
  });

  it('renders static org pages without any Google Fonts links', async () => {
    const shell = await import('../../scripts/site-shell.mjs');
    const html = shell.page({
      lang: 'zh',
      route: '/zh',
      alternates: { en: '/', zh: '/zh' },
      title: 'ZenStory AI',
      description: 'Test',
      ld: [],
      body: '<p>你好</p>',
    });

    expect(html).toContain('<link rel="stylesheet" href="/org/org.css">');
    expect(html).not.toMatch(GOOGLE_FONTS_HOST_RE);
  });
});

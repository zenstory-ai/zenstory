import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { readComposedStylesheet } from '../test/readStylesheet';

describe('reduced-motion stylesheet contract', () => {
  it('disables ambient, loading, and entrance motion without hiding progress UI', () => {
    const css = readComposedStylesheet('src/index.css');
    const reducedMotion = css.match(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\n\}/)?.[0] ?? '';

    expect(reducedMotion).toContain('.animate-float');
    expect(reducedMotion).toContain('.skeleton-shimmer');
    expect(reducedMotion).toContain('.animate-pulse');
    expect(reducedMotion).toContain('.animate-scale-in');
    expect(reducedMotion).toContain('.motion-surface-enter');
    expect(reducedMotion).toContain('.motion-surface-exit');
    expect(reducedMotion).toContain('.motion-backdrop-exit');
    expect(reducedMotion).toContain('.motion-popover-enter');
    expect(reducedMotion).toContain('.motion-popover-exit');
    expect(reducedMotion).toContain('.motion-sheet-down-exit');
    expect(reducedMotion).toContain('.motion-banner-exit');
    expect(reducedMotion).toContain('.motion-toast-enter');
    expect(reducedMotion).toContain('.motion-toast-exit');
    expect(reducedMotion).toContain('.motion-error-shake');
    expect(reducedMotion).toContain('.motion-content-reveal');
    expect(reducedMotion).toContain('.motion-collapse');
    expect(reducedMotion).toContain('.motion-segmented-indicator');
    expect(reducedMotion).toContain('--duration-instant: 0.01ms');
    expect(reducedMotion).toContain('--duration-fast: 0.01ms');
    expect(reducedMotion).toContain('--duration-base: 0.01ms');
    expect(reducedMotion).toContain('animation: none !important');
    expect(reducedMotion).not.toContain('display: none');
  });

  it('defines unified motion tokens and GPU-composited keyframes in index.css', () => {
    const css = readComposedStylesheet('src/index.css');

    for (const token of [
      '--ease-standard:',
      '--ease-out-expo:',
      '--ease-in-expo:',
      '--ease-spring-snappy:',
      '--duration-instant:',
      '--duration-fast:',
      '--duration-base:',
      '--motion-distance-xs:',
      '--motion-distance-sm:',
      '--motion-scale-subtle:',
    ]) {
      expect(css).toContain(token);
    }

    // Resize handle must keep a fixed 1px track and never animate width/height on hover
    const resizeBlock = css.slice(
      css.indexOf('.panel-resize-handle'),
      css.indexOf('/* ========================================\n   Base Styles'),
    );
    expect(resizeBlock).toContain('scaleX');
    expect(resizeBlock).toContain('transition: none;');
    expect(resizeBlock).not.toContain('hover:w-1');
    expect(resizeBlock).not.toContain('@apply w-1');
  });

  it('applies unified motion tokens and global reduced-motion rules to org-pages.css', () => {
    const orgCss = readFileSync('scripts/org-pages.css', 'utf8');

    expect(orgCss).toContain('--ease-standard:');
    expect(orgCss).toContain('--ease-out-expo:');
    expect(orgCss).toContain('--duration-instant:');
    expect(orgCss).toContain('--duration-fast:');
    expect(orgCss).toContain('--duration-base:');
    expect(orgCss).toContain('*, *::before, *::after { transition: none !important; animation: none !important; }');
  });
});

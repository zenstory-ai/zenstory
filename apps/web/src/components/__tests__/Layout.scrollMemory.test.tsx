import { cleanup, configure, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Offline translation startup runs during imports, before the per-case API fence.
// This file owns the boundary; the repository's shared setup remains unchanged.
vi.hoisted(() => {
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname;
    if (path.startsWith('/locales/')) {
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    throw new Error(`Unexpected request before offline fixture: ${path}`);
  });
});
import { Layout } from '../Layout';
import i18n from '../../lib/i18n';
import editor from '../../../public/locales/zh/editor.json';

const input = vi.hoisted(() => ({ projectId: 'A' }));
vi.mock('../../hooks/useMediaQuery', () => ({ useIsMobile: () => true, useIsTablet: () => false }));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProjectId: input.projectId }) }));
vi.mock('../../contexts/FileSearchContext', () => ({ useFileSearchContext: () => ({ openSearch: vi.fn() }) }));
vi.mock('../Header', () => ({ Header: () => null }));
vi.mock('../sidebar/Sidebar', () => ({ Sidebar: () => null }));
vi.mock('../MobileFileTree', () => ({ MobileFileTree: () => <div style={{ height: 2000 }}>local fake file leaf</div> }));
const key = (id: string) => `zenstory_mobile_scroll_positions_${id}`;
const view = () => <Layout middle={<div>editor</div>} right={<div>chat</div>} />;
const panel = () => document.getElementById('files-panel')!;
const files = () => fireEvent.click(document.getElementById('files-tab')!);
beforeEach(async () => {
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname;
    if (path.startsWith('/locales/')) return new Response('{}', { status: 200 });
    throw new Error(`Unexpected offline scroll request: ${path}`);
  });
  sessionStorage.clear(); input.projectId = 'A';
  sessionStorage.setItem(key('A'), JSON.stringify({ files: 100, editor: 40 }));
  i18n.addResourceBundle('zh', 'editor', editor, true, true); await i18n.changeLanguage('zh');
});
afterEach(() => { cleanup(); sessionStorage.clear(); configure({ reactStrictMode: false }); vi.unstubAllGlobals(); });

describe.each([false, true])('actual mobile container project scroll Strict=%s', strict => {
  beforeEach(() => configure({ reactStrictMode: strict }));
  it.each([false, true])('restores B without changing active panel; savedB=%s', saved => {
    if (saved) sessionStorage.setItem(key('B'), JSON.stringify({ files: 35 }));
    const { rerender } = render(view()); files();
    const element = panel();
    expect(element.scrollTop).toBe(100);
    const a = sessionStorage.getItem(key('A'));
    input.projectId = 'B'; rerender(view());
    expect(panel()).toBe(element);
    expect(document.getElementById('files-tab')).toHaveAttribute('aria-selected', 'true');
    expect(element.scrollTop).toBe(saved ? 35 : 0);
    element.scrollTop = 66; fireEvent.scroll(element);
    expect(JSON.parse(sessionStorage.getItem(key('B'))!)).toEqual({ files: 66 });
    expect(sessionStorage.getItem(key('A'))).toBe(a);
    input.projectId = 'A'; rerender(view());
    expect(panel()).toBe(element);
    expect(element.scrollTop).toBe(100);
    expect(sessionStorage.getItem(key('A'))).toBe(a);
  });
  it('keeps same-project active panel restoration and mounted containers', () => {
    render(view()); files(); const element = panel();
    element.scrollTop = 80; fireEvent.scroll(element);
    fireEvent.click(document.getElementById('editor-tab')!);
    expect(screen.getByText('editor')).toBeInTheDocument();
    expect(panel()).toBe(element);
    element.scrollTop = 0; files();
    expect(element.scrollTop).toBe(80);
    expect(JSON.parse(sessionStorage.getItem(key('A'))!)).toEqual({ files: 80, editor: 40 });
  });
});

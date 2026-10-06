import { act, configure, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useScrollMemory } from '../useScrollMemory';
import { logger } from '../../lib/logger';

const key = (id: string) => `zenstory_mobile_scroll_positions_${id}`;
beforeEach(() => { sessionStorage.clear(); vi.spyOn(logger, 'warn').mockImplementation(() => {}); });
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); vi.restoreAllMocks(); configure({ reactStrictMode: false }); });

describe.each([false, true])('scroll namespaces, root Strict=%s', strict => {
  beforeEach(() => configure({ reactStrictMode: strict }));
  it.each(['missing', 'malformed'])('does not inherit A positions when B is %s', kind => {
    const { result, rerender } = renderHook(({ id }) => useScrollMemory(id), { initialProps: { id: 'A' } });
    act(() => { result.current.saveScrollPosition('editor', 240); result.current.saveScrollPosition('files', 100); });
    const a = sessionStorage.getItem(key('A'));
    if (kind === 'malformed') sessionStorage.setItem(key('B'), '{');
    rerender({ id: 'B' });
    expect(result.current.getScrollPosition('editor')).toBe(0);
    expect(result.current.getScrollPosition('files')).toBe(0);
    act(() => result.current.saveScrollPosition('chat', 40));
    expect(JSON.parse(sessionStorage.getItem(key('B'))!)).toEqual({ chat: 40 });
    expect(sessionStorage.getItem(key('A'))).toBe(a);
  });
  it('loads saved B only and restores A after returning', () => {
    sessionStorage.setItem(key('B'), JSON.stringify({ chat: 35 }));
    const { result, rerender } = renderHook(({ id }) => useScrollMemory(id), { initialProps: { id: 'A' } });
    act(() => result.current.saveScrollPosition('editor', 240));
    rerender({ id: 'B' });
    expect(result.current.getScrollPosition('chat')).toBe(35);
    expect(result.current.getScrollPosition('editor')).toBe(0);
    rerender({ id: 'A' });
    const element = document.createElement('div');
    result.current.restoreScrollPosition('editor', element);
    result.current.restoreScrollPosition('editor', null);
    expect(element.scrollTop).toBe(240);
    expect(result.current.getScrollPosition('chat')).toBe(0);
  });
  it('uses default namespace and preserves in-memory state on persistence failure', () => {
    const { result } = renderHook(() => useScrollMemory());
    const storage = sessionStorage;
    vi.stubGlobal('sessionStorage', { getItem: storage.getItem.bind(storage), setItem: () => { throw new Error('offline storage write'); } });
    act(() => result.current.saveScrollPosition('files', 80));
    expect(result.current.getScrollPosition('files')).toBe(80);
    expect(result.current.getScrollPosition('unknown')).toBe(0);
    expect(sessionStorage.getItem(key('default'))).toBeNull();
    vi.unstubAllGlobals();
    act(() => result.current.saveScrollPosition('chat', 20));
    expect(JSON.parse(sessionStorage.getItem(key('default'))!)).toEqual({ files: 80, chat: 20 });
  });
});

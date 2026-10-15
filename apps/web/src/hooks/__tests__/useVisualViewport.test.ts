import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useVisualViewport } from '../useVisualViewport';

describe('useVisualViewport', () => {
  let viewport: EventTarget & { height: number; offsetTop: number; scale: number };
  let frame: FrameRequestCallback | undefined;
  const style = document.documentElement.style;
  const height = () => style.getPropertyValue('--app-viewport-height');
  const top = () => style.getPropertyValue('--app-viewport-top');
  const flush = () => act(() => { const callback = frame; frame = undefined; callback?.(0); });

  beforeEach(() => {
    frame = undefined;
    viewport = Object.assign(new EventTarget(), { height: 844, offsetTop: 0, scale: 1 });
    vi.stubGlobal('visualViewport', viewport);
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { frame = callback; return 1; }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn(() => { frame = undefined; }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    style.removeProperty('--app-viewport-height');
    style.removeProperty('--app-viewport-top');
  });

  it('tracks keyboard height and independent Safari viewport panning, then restores', () => {
    renderHook(() => useVisualViewport());
    expect(height()).toBe('844px');
    expect(top()).toBe('0px');
    Object.assign(viewport, { height: 400, offsetTop: 160 });
    act(() => {
      viewport.dispatchEvent(new Event('resize'));
      viewport.dispatchEvent(new Event('scroll'));
    });
    expect(requestAnimationFrame).toHaveBeenCalledTimes(1);
    flush();
    expect(height()).toBe('400px');
    expect(top()).toBe('160px');
    viewport.offsetTop = 200;
    act(() => viewport.dispatchEvent(new Event('scroll')));
    flush();
    expect(top()).toBe('200px');
    Object.assign(viewport, { height: 844, offsetTop: 0 });
    act(() => viewport.dispatchEvent(new Event('resize')));
    flush();
    expect(height()).toBe('844px');
    expect(top()).toBe('0px');
  });

  it('leaves pinch zoom to the browser instead of reflowing the app as a keyboard', () => {
    renderHook(() => useVisualViewport());
    Object.assign(viewport, { height: 422, offsetTop: 100, scale: 2 });
    act(() => viewport.dispatchEvent(new Event('resize')));
    flush();
    expect(height()).toBe('844px');
    expect(top()).toBe('0px');
    Object.assign(viewport, { height: 400, offsetTop: 160, scale: 1 });
    act(() => viewport.dispatchEvent(new Event('resize')));
    flush();
    expect(height()).toBe('400px');
    expect(top()).toBe('160px');
  });

  it('uses window resize when VisualViewport is unavailable', () => {
    vi.stubGlobal('visualViewport', undefined);
    vi.stubGlobal('innerHeight', 700);
    renderHook(() => useVisualViewport());
    expect(height()).toBe('700px');
    vi.stubGlobal('innerHeight', 360);
    act(() => window.dispatchEvent(new Event('resize')));
    flush();
    expect(height()).toBe('360px');
    expect(top()).toBe('0px');
  });

  it('restores full layout height when the keyboard closes while still zoomed', () => {
    renderHook(() => useVisualViewport());
    Object.assign(viewport, { height: 400, offsetTop: 160 });
    act(() => viewport.dispatchEvent(new Event('resize')));
    flush();
    Object.assign(viewport, { height: 200, offsetTop: 100, scale: 2 });
    act(() => viewport.dispatchEvent(new Event('resize')));
    flush();
    expect(height()).toBe('400px');
    expect(top()).toBe('0px');
    viewport.height = 422;
    act(() => viewport.dispatchEvent(new Event('resize')));
    flush();
    expect(height()).toBe('844px');
    expect(top()).toBe('0px');
  });

  it('cancels queued work, removes listeners and restores preexisting CSS on unmount', () => {
    style.setProperty('--app-viewport-height', '70dvh');
    const remove = vi.spyOn(viewport, 'removeEventListener');
    const { unmount } = renderHook(() => useVisualViewport());
    act(() => viewport.dispatchEvent(new Event('resize')));
    unmount();
    expect(cancelAnimationFrame).toHaveBeenCalledWith(1);
    expect(remove).toHaveBeenCalledWith('resize', expect.any(Function));
    expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function));
    expect(height()).toBe('70dvh');
    expect(top()).toBe('');
    viewport.height = 300;
    act(() => viewport.dispatchEvent(new Event('resize')));
    expect(frame).toBeUndefined();
  });
});

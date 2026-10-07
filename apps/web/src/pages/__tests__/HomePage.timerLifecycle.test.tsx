import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { createInstance, type i18n } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import enHome from '../../../public/locales/en/home.json';
import enCommon from '../../../public/locales/en/common.json';
import enPrivacy from '../../../public/locales/en/privacy.json';
import enDashboard from '../../../public/locales/en/dashboard.json';

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, logout: vi.fn() }),
}));

import HomePage from '../HomePage';

const reducedQuery = '(prefers-reduced-motion: reduce)';
let language: i18n;
let offlineFetch: ReturnType<typeof vi.fn>;
const media = new Map<string, {
  list: MediaQueryList;
  listeners: Set<EventListenerOrEventListenerObject>;
}>();

function mediaList(query: string): MediaQueryList {
  const existing = media.get(query);
  if (existing) return existing.list;
  const listeners = new Set<EventListenerOrEventListenerObject>();
  const list = {
    matches: query === '(min-width: 768px)',
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: (_type: string, listener: EventListenerOrEventListenerObject) => listeners.add(listener),
    removeEventListener: (_type: string, listener: EventListenerOrEventListenerObject) => listeners.delete(listener),
    dispatchEvent: (event: Event) => {
      for (const listener of listeners) {
        if (typeof listener === 'function') listener.call(list, event);
        else listener.handleEvent(event);
      }
      return true;
    },
  } as MediaQueryList;
  media.set(query, { list, listeners });
  return list;
}

function reducedMotion(matches: boolean) {
  act(() => {
    const list = mediaList(reducedQuery);
    Object.defineProperty(list, 'matches', { value: matches, configurable: true });
    const event = new Event('change');
    Object.defineProperty(event, 'matches', { value: matches });
    list.dispatchEvent(event);
  });
}

beforeEach(async () => {
  vi.stubGlobal('localStorage', new window.Storage());
  vi.stubGlobal('sessionStorage', new window.Storage());
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false);
  offlineFetch = vi.fn(() => { throw new Error('Unexpected HomePage fixture network'); });
  vi.stubGlobal('fetch', offlineFetch);
  vi.spyOn(window, 'matchMedia').mockImplementation(mediaList);
  language = createInstance();
  await language.init({ lng: 'en', fallbackLng: 'en', initImmediate: false,
    resources: { en: { home: enHome, common: enCommon, privacy: enPrivacy, dashboard: enDashboard } },
  });
  vi.useFakeTimers();
  vi.setSystemTime(0);
});

afterEach(() => {
  cleanup();
  expect([...media.values()].every(({ listeners }) => listeners.size === 0)).toBe(true);
  // The pre-repair unmount cases intentionally expose a pending timeout.
  // Drain it only after unmount, then restore all test-owned facilities.
  act(() => { vi.runOnlyPendingTimers(); });
  expect(vi.getTimerCount()).toBe(0);
  expect(offlineFetch).not.toHaveBeenCalled();
  media.clear();
  language.off('languageChanged');
  localStorage.clear();
  sessionStorage.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function show(strict: boolean) {
  const page = <MemoryRouter><I18nextProvider i18n={language}><HomePage /></I18nextProvider></MemoryRouter>;
  return render(strict ? <StrictMode>{page}</StrictMode> : page);
}

function advance(milliseconds: number) {
  act(() => { vi.advanceTimersByTime(milliseconds); });
}

function select(scene: 'create' | 'suggest' | 'edit') {
  fireEvent.click(screen.getByRole('button', { name: enHome.demo.scenes[scene] }));
}

function expectScene(scene: 'create' | 'suggest' | 'edit') {
  expect(screen.getByRole('button', { name: enHome.demo.scenes[scene] }).className).toContain('text-white');
  const content = {
    create: enHome.preview.sceneCreate.ai.created,
    suggest: enHome.preview.sceneSuggest.ai.writing,
    edit: enHome.preview.sceneEdit.ai.updated,
  };
  expect(screen.getByText(content[scene])).toBeInTheDocument();
  for (const other of ['create', 'suggest', 'edit'] as const) {
    if (other !== scene) expect(screen.queryByText(content[other])).not.toBeInTheDocument();
  }
}

describe.each([false, true])('HomePage real timer lifetime (root StrictMode=%s)', (strict) => {
  it('a newer manual scene replaces an already pending automatic transition', () => {
    show(strict);
    advance(5040); // First 80ms interval tick at/after the 5000ms deadline.
    expectScene('create');
    advance(40);
    select('edit');
    advance(300);
    expectScene('edit');
    advance(300);
    expectScene('edit');
  });

  it('manual selection at the automatic deadline remains selected after the old cycle finishes', () => {
    show(strict);
    advance(5000);
    select('edit'); // Manual completion at 5300; old automatic completion at 5340.
    advance(300);
    expectScene('edit');
    advance(40);
    expectScene('edit');
  });

  it('rapid distinct manual clicks retain the latest scene and the 300ms transition', () => {
    show(strict);
    select('edit');
    advance(100);
    select('suggest');
    advance(299);
    advance(1);
    expectScene('suggest');
    advance(400);
    expectScene('suggest');
  });

  it('enabling reduced motion revokes an automatic pending transition', () => {
    show(strict);
    advance(5040);
    reducedMotion(true);
    expectScene('create');
    advance(300);
    expectScene('create');
  });

  it('enabling reduced motion revokes an old manual transition and preserves a new immediate choice', () => {
    show(strict);
    select('edit');
    advance(100);
    reducedMotion(true);
    select('suggest');
    expectScene('suggest');
    advance(300);
    expectScene('suggest');
  });

  it.each(['automatic', 'manual'] as const)('actual unmount releases the %s pending transition', (kind) => {
    const page = show(strict);
    if (kind === 'automatic') advance(5040);
    else select('edit');
    expectScene('create');
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    page.unmount();
    expect(screen.queryByRole('button', { name: enHome.demo.scenes.create })).not.toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('ordinary automatic/manual/no-op/progress and reduced-motion selection remain working', () => {
    const { container } = show(strict);
    expectScene('create');
    select('create');
    expectScene('create');
    advance(80);
    expect(container.querySelector('div[style*="width"]')?.getAttribute('style')).toContain('1.6%');
    advance(4960);
    advance(299);
    expectScene('create');
    advance(1);
    expectScene('suggest');
    select('edit');
    advance(299);
    expectScene('suggest');
    advance(1);
    expectScene('edit');
    reducedMotion(true);
    select('create');
    expectScene('create');
    expect(container.querySelector('div[style*="width"]')).toBeNull();
    advance(20000);
    expectScene('create');
  });
});

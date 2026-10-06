import { act, cleanup, configure, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom';
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
import zh from '../../../public/locales/zh/onboarding.json';
import common from '../../../public/locales/zh/common.json';
import { AuthIdentityQueryBoundary, AuthProvider, type User } from '../../contexts/AuthContext';
import { clearAuthStorage } from '../../lib/apiClient';
import i18n from '../../lib/i18n';
import { getPersonaOnboardingData } from '../../lib/onboardingPersona';
import { personaOnboardingQueryKey, type PersonaOnboardingProfile, type PersonaOnboardingUpsertRequest } from '../../lib/onboardingPersonaApi';
import OnboardingPersonaPage from '../OnboardingPersonaPage';
import { toast } from '../../lib/toast';
vi.mock('../../lib/toast', () => ({ toast: { error: vi.fn() } }));

vi.mock('../../lib/analytics', () => ({ identifyUser: vi.fn(), resetAnalytics: vi.fn(),
  trackEvent: vi.fn(), captureException: vi.fn() }));

const user: User = { id: 'persona-hydration-A', username: 'offline-author', email: 'offline@example.test',
  email_verified: true, is_active: true, is_superuser: false,
  created_at: '2026-10-06T00:00:00Z', updated_at: '2026-10-06T00:00:00Z' };
const profile: PersonaOnboardingProfile = { version: 1, completed_at: '2026-10-06T00:00:00Z',
  selected_personas: ['explorer'], selected_goals: ['monetize'], experience_level: 'intermediate', skipped: false };
const state = (value: PersonaOnboardingProfile) => ({ required: false, rollout_at: '2026-03-05T16:00:00Z',
  new_user_window_days: 7, profile: value, recommendations: [] });
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status,
  headers: { 'Content-Type': 'application/json' } });
function deferred() {
  let resolve!: (value: Response) => void;
  let settled = false;
  const promise = new Promise<Response>(done => { resolve = value => { settled = true; done(value); }; });
  return { promise, resolve, get settled() { return settled; } };
}
let requests: { path: string; method: string; authorization: string | null; body: string | null }[];
let pending: ReturnType<typeof deferred>[];
let clients: QueryClient[];
let saved: PersonaOnboardingUpsertRequest[];
let unknown: string[];

beforeEach(async () => {
  clearAuthStorage(); localStorage.clear(); sessionStorage.clear();
  localStorage.setItem('access_token', 'offline-persona-A');
  localStorage.setItem('refresh_token', 'offline-persona-refresh-A');
  localStorage.setItem('user', JSON.stringify(user));
  window.history.replaceState(null, '', '/onboarding/persona');
  requests = []; pending = []; clients = []; saved = []; unknown = []; vi.mocked(toast.error).mockClear();
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(typeof input === 'string' || input instanceof URL ? String(input) : input.url,
      'http://local.test').pathname;
    if (path.startsWith('/locales/')) return Promise.resolve(response({}));
    const method = init?.method ?? 'GET';
    requests.push({ path, method, authorization: new Headers(init?.headers).get('Authorization'),
      body: typeof init?.body === 'string' ? init.body : null });
    if (path === '/api/auth/me') return Promise.resolve(response(user));
    if (path === '/api/v1/persona/onboarding' && method === 'GET') return Promise.resolve(response(state(profile)));
    if (path === '/api/v1/persona/onboarding' && method === 'PUT') {
      const payload = JSON.parse(String(init?.body)) as PersonaOnboardingUpsertRequest;
      saved.push(payload);
      const held = deferred(); pending.push(held); return held.promise;
    }
    unknown.push(`${method} ${path}`);
    throw new Error('Unexpected offline persona request: ' + path);
  }));
  i18n.addResourceBundle('zh', 'onboarding', zh, true, true);
  i18n.addResourceBundle('zh', 'common', common, true, true);
  await i18n.changeLanguage('zh');

});

afterEach(async () => {
  cleanup();
  await act(async () => { for (const held of pending) if (!held.settled) held.resolve(response({}, 503)); });
  for (const client of clients) client.clear();
  clearAuthStorage(); localStorage.clear(); sessionStorage.clear();
  vi.unstubAllGlobals(); configure({ reactStrictMode: false });
  window.history.replaceState(null, '', '/');
  expect(unknown).toEqual([]);
  expect(pending.every(held => held.settled)).toBe(true);
});

async function mount(strict: boolean) {
  configure({ reactStrictMode: strict });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity,
    refetchOnWindowFocus: false } } });
  clients.push(client);
  render(<QueryClientProvider client={client}><AuthProvider><AuthIdentityQueryBoundary><BrowserRouter><Link to="/manual?chosen=author#keep">Leave persona</Link><Routes>
    <Route path="/onboarding/persona" element={<OnboardingPersonaPage />} />
    <Route path="/manual" element={<div>author chosen destination</div>} />
    <Route path="/dashboard" element={<div>owned persona completed</div>} />
  </Routes></BrowserRouter></AuthIdentityQueryBoundary></AuthProvider></QueryClientProvider>);
  await screen.findByRole('heading', { name: zh.hero.title });
  await waitFor(() => expect(persona('explorer')).toHaveAttribute('aria-pressed', 'true'));
}
const persona = (id: 'explorer' | 'serial') => screen.getByRole('button', {
  name: new RegExp(zh.persona.options[id].title) });
async function submit() {
  fireEvent.click(persona('explorer'));
  fireEvent.click(persona('serial'));
  fireEvent.click(screen.getByRole('button', { name: zh.actions.submit }));
  await waitFor(() => expect(pending).toHaveLength(1));
  expect(saved).toEqual([{ selected_personas: ['serial'], selected_goals: ['monetize'],
    experience_level: 'intermediate', skipped: false }]);
}
async function finish(status: number) {
  await act(async () => {
    pending[0].resolve(response(status === 200 ? state({ ...profile, ...saved[0], completed_at: '2026-10-06T01:00:00Z' }) : { error: 'owned fake failure' }, status));
    await pending[0].promise;
  });
}
describe.each([false, true])('actual persona save continuation Strict=%s', strict => {
  it.each([200, 503])('does not override route departure after save returns %s', async status => {
    await mount(strict); await submit();
    fireEvent.click(screen.getByRole('link', { name: 'Leave persona' }));
    await screen.findByText('author chosen destination');
    const entry = window.location.pathname + window.location.search + window.location.hash;
    const storage = structuredClone(getPersonaOnboardingData(user.id));
    const cached = structuredClone(clients[0].getQueryData(personaOnboardingQueryKey(user.id)));
    await finish(status);
    expect(getPersonaOnboardingData(user.id)).toEqual(storage);
    expect(clients[0].getQueryData(personaOnboardingQueryKey(user.id))).toEqual(cached);
    console.info('PERSONA_SAVE_CONTINUATION', JSON.stringify({ strict, status, requests,
      before: entry, after: window.location.pathname + window.location.search + window.location.hash,
      toast: vi.mocked(toast.error).mock.calls }));
    expect(window.location.pathname + window.location.search + window.location.hash).toBe(entry);
    expect(screen.getByText('author chosen destination')).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
    expect(saved).toHaveLength(1);
  });
  it('retains current successful save, cache, profile and destination', async () => {
    await mount(strict); await submit(); await finish(200);
    await screen.findByText('owned persona completed');
    expect(getPersonaOnboardingData(user.id)).toMatchObject({ selected_personas: ['serial'],
      selected_goals: ['monetize'], experience_level: 'intermediate', skipped: false });
    expect(clients[0].getQueryData(personaOnboardingQueryKey(user.id))).toEqual(state({ ...profile, ...saved[0], completed_at: '2026-10-06T01:00:00Z' }));
    expect(window.location.pathname).toBe('/dashboard');
    expect(toast.error).not.toHaveBeenCalled();
  });
});

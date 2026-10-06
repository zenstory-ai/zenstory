import { act, cleanup, configure, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
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
import { getPersonaOnboardingData, savePersonaOnboardingData } from '../../lib/onboardingPersona';
import { personaOnboardingQueryKey, type PersonaOnboardingProfile, type PersonaOnboardingUpsertRequest } from '../../lib/onboardingPersonaApi';
import OnboardingPersonaPage from '../OnboardingPersonaPage';

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
  requests = []; pending = []; clients = []; saved = []; unknown = [];
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(typeof input === 'string' || input instanceof URL ? String(input) : input.url,
      'http://local.test').pathname;
    if (path.startsWith('/locales/')) return Promise.resolve(response({}));
    const method = init?.method ?? 'GET';
    requests.push({ path, method, authorization: new Headers(init?.headers).get('Authorization'),
      body: typeof init?.body === 'string' ? init.body : null });
    if (path === '/api/auth/me') return Promise.resolve(response(user));
    if (path === '/api/v1/persona/onboarding' && method === 'GET') {
      const held = deferred(); pending.push(held); return held.promise;
    }
    if (path === '/api/v1/persona/onboarding' && method === 'PUT') {
      const payload = JSON.parse(String(init?.body)) as PersonaOnboardingUpsertRequest;
      saved.push(payload);
      return Promise.resolve(response(state({ ...payload, version: 1, completed_at: profile.completed_at })));
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
  render(<QueryClientProvider client={client}><AuthProvider><AuthIdentityQueryBoundary><BrowserRouter><Routes>
    <Route path="/onboarding/persona" element={<OnboardingPersonaPage />} />
    <Route path="/dashboard" element={<div>owned persona completed</div>} />
  </Routes></BrowserRouter></AuthIdentityQueryBoundary></AuthProvider></QueryClientProvider>);
  await screen.findByRole('heading', { name: zh.hero.title });
  await waitFor(() => expect(pending).toHaveLength(1));
}
const persona = (id: 'explorer' | 'serial') => screen.getByRole('button', {
  name: new RegExp(zh.persona.options[id].title) });
const goal = (id: 'buildHabit' | 'monetize') => screen.getByRole('button', { name: zh.goal.options[id].title });
const level = (id: 'advanced' | 'intermediate') => screen.getByRole('radio', {
  name: new RegExp(zh.experience.options[id].title) });
async function hydrate() {
  await act(async () => pending[0].resolve(response(state(profile))));
  await screen.findByText(zh.hero.restore);
}

describe.each([false, true])('actual persona hydration, root Strict=%s', strict => {
  it.each(['personas', 'goals', 'experience'] as const)('preserves edited %s and hydrates untouched fields', async field => {
    await mount(strict);
    if (field === 'personas') fireEvent.click(persona('serial'));
    if (field === 'goals') fireEvent.click(goal('buildHabit'));
    if (field === 'experience') fireEvent.click(level('advanced'));
    await hydrate();
    await waitFor(() => expect(field === 'experience' ? goal('monetize') : level('intermediate'))
      .toHaveAttribute(field === 'experience' ? 'aria-pressed' : 'aria-checked', 'true'));
    const expected: PersonaOnboardingUpsertRequest = {
      selected_personas: field === 'personas' ? ['serial'] : ['explorer'],
      selected_goals: field === 'goals' ? ['buildHabit'] : ['monetize'],
      experience_level: field === 'experience' ? 'advanced' : 'intermediate', skipped: false,
    };
    console.info('PERSONA_HYDRATION', JSON.stringify({ strict, field, requests,
      serial: persona('serial').getAttribute('aria-pressed'),
      buildHabit: goal('buildHabit').getAttribute('aria-pressed'),
      advanced: level('advanced').getAttribute('aria-checked'), expected }));
    expect(persona(field === 'personas' ? 'serial' : 'explorer')).toHaveAttribute('aria-pressed', 'true');
    expect(goal(field === 'goals' ? 'buildHabit' : 'monetize')).toHaveAttribute('aria-pressed', 'true');
    expect(level(field === 'experience' ? 'advanced' : 'intermediate')).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('button', { name: zh.actions.submit }));
    await screen.findByText('owned persona completed');
    expect(saved).toEqual([expected]);
    expect(requests.filter(item => item.path === '/api/v1/persona/onboarding')
      .every(item => item.authorization === 'Bearer offline-persona-A')).toBe(true);
  });

  it.each([false, true])('hydrates an untouched form; cached initial profile=%s', async cached => {
    if (cached) savePersonaOnboardingData(user.id, { selected_personas: ['serial'],
      selected_goals: ['buildHabit'], experience_level: 'advanced', skipped: false });
    await mount(strict);
    if (cached) expect(persona('serial')).toHaveAttribute('aria-pressed', 'true');
    await hydrate();
    await waitFor(() => expect(level('intermediate')).toHaveAttribute('aria-checked', 'true'));
    expect(persona('explorer')).toHaveAttribute('aria-pressed', 'true');
    expect(persona('serial')).toHaveAttribute('aria-pressed', 'false');
    expect(goal('monetize')).toHaveAttribute('aria-pressed', 'true');
    expect(level('intermediate')).toHaveAttribute('aria-checked', 'true');
  });
});

it('keeps all cached-then-edited fields through two real GETs while caching server truth', async () => {
  savePersonaOnboardingData(user.id, { selected_personas: [], selected_goals: [],
    experience_level: 'beginner', skipped: true });
  await mount(false);
  fireEvent.click(persona('serial'));
  fireEvent.click(goal('buildHabit'));
  fireEvent.click(level('advanced'));
  await hydrate();
  await waitFor(() => expect(getPersonaOnboardingData(user.id)).toMatchObject({
    selected_personas: ['explorer'], selected_goals: ['monetize'], experience_level: 'intermediate', skipped: false,
  }));
  expect(persona('serial')).toHaveAttribute('aria-pressed', 'true');
  const refetch = clients[0].refetchQueries({ queryKey: personaOnboardingQueryKey(user.id), exact: true });
  await waitFor(() => expect(pending).toHaveLength(2));
  const next: PersonaOnboardingProfile = { ...profile, selected_personas: ['professional'],
    selected_goals: ['finishBook'], experience_level: 'beginner' };
  await act(async () => { pending[1].resolve(response(state(next))); await refetch; });
  await waitFor(() => expect(getPersonaOnboardingData(user.id)).toMatchObject({
    selected_personas: ['professional'], selected_goals: ['finishBook'], experience_level: 'beginner', skipped: false,
  }));
  expect(persona('serial')).toHaveAttribute('aria-pressed', 'true');
  expect(goal('buildHabit')).toHaveAttribute('aria-pressed', 'true');
  expect(level('advanced')).toHaveAttribute('aria-checked', 'true');
  fireEvent.click(screen.getByRole('button', { name: zh.actions.submit }));
  await screen.findByText('owned persona completed');
  expect(saved).toEqual([{ selected_personas: ['serial'], selected_goals: ['buildHabit'],
    experience_level: 'advanced', skipped: false }]);
});

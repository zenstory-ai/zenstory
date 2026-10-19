import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.hoisted(() => {
  vi.stubGlobal('fetch', async () => new Response('{}', {
    headers: { 'Content-Type': 'application/json' },
  }));
});
import onboarding from '../../../public/locales/en/onboarding.json';
import dashboard from '../../../public/locales/en/dashboard.json';
import common from '../../../public/locales/en/common.json';
import { AuthIdentityQueryBoundary, AuthProvider, useAuth } from '../../contexts/AuthContext';
import { FirstWorkEntry } from '../../components/dashboard/FirstWorkEntry';
import { useDashboardProjectType } from '../../hooks/useDashboardProjectType';
import { clearAuthStorage } from '../../lib/apiClient';
import i18n from '../../lib/i18n';
import { getPreferredProjectType, setPreferredProjectType } from '../../lib/preferredProjectType';
import OnboardingPersonaPage from '../OnboardingPersonaPage';

vi.mock('../../lib/analytics', () => ({ identifyUser: vi.fn(), resetAnalytics: vi.fn(),
  trackEvent: vi.fn(), captureException: vi.fn() }));

// Keep the actual authentication/identity boundary, onboarding save, type hook,
// and first-work HTTP client together. Only the network is replaced.
const json = (body: unknown) => new Response(JSON.stringify(body), {
  headers: { 'Content-Type': 'application/json' },
});
const account = (id: string) => ({ id, username: id, email: `${id}@example.invalid`,
  email_verified: true, is_active: true, is_superuser: false,
  created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z' });
const pending = { status: 'pending', project_id: null, file_id: null, available: false,
  static_guidance: false, opened_at: null, first_manual_write_at: null, first_ai_requested_at: null };
let initialized: { owner: string; type: string }[];
let unknown: string[];
let client: QueryClient;

function SessionControls() {
  const auth = useAuth();
  const navigate = useNavigate();
  return <>
    <span>{auth.loading ? 'auth-loading' : 'auth-ready'}</span>
    {['A', 'B'].map(id => <button key={id} onClick={async () => {
      await auth.login(id, 'offline-password'); navigate('/onboarding/persona');
    }}>Log in {id}</button>)}
    <button onClick={() => { auth.logout(); navigate('/login'); }}>Log out</button>
    <button onClick={async () => { await auth.refreshToken(); }}>Refresh identity</button>
  </>;
}

function DashboardEntry() {
  const { user } = useAuth();
  const { activeTab } = useDashboardProjectType([]);
  return <>
    <output aria-label="active project type">{activeTab}</output>
    <FirstWorkEntry userId={user?.id ?? null} preferredType={activeTab} onCreated={async () => {}} />
  </>;
}

beforeEach(async () => {
  clearAuthStorage(); localStorage.clear(); sessionStorage.clear();
  initialized = []; unknown = [];
  window.history.replaceState({}, '', '/login');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(input instanceof Request ? input.url : String(input), window.location.origin).pathname;
    if (path.startsWith('/locales/')) return json({});
    const method = init?.method ?? 'GET';
    const owner = new Headers(init?.headers).get('Authorization')?.replace('Bearer offline-', '') ?? '';
    if (path === '/api/auth/login') {
      const id = String((init?.body as FormData).get('username'));
      return json({ access_token: `offline-${id}`, refresh_token: `offline-refresh-${id}`, user: account(id) });
    }
    if (path === '/api/auth/logout') return json({});
    if (path === '/api/auth/refresh') {
      const body = JSON.parse(String(init?.body));
      const id = String(body.refresh_token).replace('offline-refresh-', '');
      return json({ access_token: `offline-${id}`, refresh_token: `offline-refresh-${id}`, user: account(id) });
    }
    if (path === '/api/v1/persona/onboarding') {
      if (method === 'GET') return json({ required: true, profile: null });
      const body = JSON.parse(String(init?.body));
      return json({ required: false, profile: { ...body, version: 1,
        completed_at: '2026-10-14T00:00:00Z', experience_level: body.experience_level ?? 'beginner' } });
    }
    if (path === '/api/v1/workspace/first-work') {
      if (method === 'GET') return json(pending);
      const body = JSON.parse(String(init?.body));
      initialized.push({ owner, type: body.preferred_type });
      return json({ ...pending, status: 'created', project_id: `${owner}-project`, file_id: `${owner}-draft`,
        available: true, static_guidance: true });
    }
    unknown.push(`${method} ${path}`);
    throw new Error(`Unexpected offline preference request: ${method} ${path}`);
  }));
  for (const [namespace, data] of Object.entries({ onboarding, dashboard, common })) {
    i18n.addResourceBundle('en', namespace, data, true, true);
  }
  await i18n.changeLanguage('en');
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><AuthProvider><BrowserRouter>
    <SessionControls /><AuthIdentityQueryBoundary><Routes>
      <Route path="/login" element={<div>offline login</div>} />
      <Route path="/onboarding/persona" element={<OnboardingPersonaPage />} />
      <Route path="/dashboard" element={<DashboardEntry />} />
    </Routes></AuthIdentityQueryBoundary>
  </BrowserRouter></AuthProvider></QueryClientProvider>);
  await screen.findByText('auth-ready');
});

afterEach(() => {
  cleanup(); client.clear(); clearAuthStorage(); localStorage.clear(); sessionStorage.clear();
  vi.unstubAllGlobals();
  expect(unknown).toEqual([]);
});

/** Log in and answer the one-question page: tap a type, or skip it. */
async function logInAndFinishProfile(owner: string, answer: 'novel' | 'short' | 'screenplay' | 'skip') {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: `Log in ${owner}` })); });
  await screen.findByRole('heading', { name: onboarding.start.title });
  fireEvent.click(answer === 'skip'
    ? screen.getByRole('button', { name: onboarding.actions.skip })
    : screen.getByRole('button', { name: new RegExp(onboarding.start.options[answer].title) }));
  await screen.findByRole('button', { name: dashboard.firstWork.open });
  await waitFor(() => expect(initialized.some(item => item.owner === owner)).toBe(true));
}

describe('first-work type belongs to the current account', () => {
  it.each(['novel', 'screenplay'] as const)('does not pass A short-story memory to new B choosing %s', async (type) => {
    setPreferredProjectType('short'); // A's deliberate public entry before authentication.
    await logInAndFinishProfile('A', 'short');
    expect(initialized).toEqual([{ owner: 'A', type: 'short' }]);
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    await screen.findByText('offline login');

    await logInAndFinishProfile('B', type);
    expect(initialized).toEqual([{ owner: 'A', type: 'short' }, { owner: 'B', type }]);
    expect(screen.getByLabelText('active project type')).toHaveTextContent(type);
    expect(getPreferredProjectType('A')).toBe('short');
    expect(getPreferredProjectType('B')).toBe(type);
  });

  it("keeps B's fresh public entry when B skips the question", async () => {
    setPreferredProjectType('short', 'B'); // B's older, still-valid creation memory.
    setPreferredProjectType('short');
    await logInAndFinishProfile('A', 'short');
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    await screen.findByText('offline login');
    setPreferredProjectType('screenplay');

    await logInAndFinishProfile('B', 'skip');
    expect(initialized.at(-1)).toEqual({ owner: 'B', type: 'screenplay' });
  });

  it("keeps B's valid creation memory when B skips the question", async () => {
    setPreferredProjectType('screenplay', 'B');
    setPreferredProjectType('short');
    await logInAndFinishProfile('A', 'short');
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    await screen.findByText('offline login');

    await logInAndFinishProfile('B', 'skip');
    expect(initialized.at(-1)).toEqual({ owner: 'B', type: 'screenplay' });
  });

  it("B's tap on the question is the latest explicit choice, ahead of older memory and public entries", async () => {
    setPreferredProjectType('screenplay', 'B');
    setPreferredProjectType('short');

    await logInAndFinishProfile('B', 'novel');
    expect(initialized.at(-1)).toEqual({ owner: 'B', type: 'novel' });
    expect(getPreferredProjectType('B')).toBe('novel');
  });

  it('does not let A same-identity refresh claim an anonymous entry intended for B', async () => {
    setPreferredProjectType('screenplay');
    await logInAndFinishProfile('A', 'screenplay');
    // A stale anonymous tab can write a fresh entry while A is already signed in.
    const aTabStorage = sessionStorage;
    vi.stubGlobal('sessionStorage', new window.Storage());
    setPreferredProjectType('short');
    vi.stubGlobal('sessionStorage', aTabStorage);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh identity' })); });

    expect(getPreferredProjectType()).toBe('short');
    expect(getPreferredProjectType('A')).toBe('screenplay');
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    await screen.findByText('offline login');
    await logInAndFinishProfile('B', 'skip');
    expect(initialized.at(-1)).toEqual({ owner: 'B', type: 'short' });
  });
});

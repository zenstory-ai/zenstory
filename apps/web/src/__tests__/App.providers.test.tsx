import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  path: '/admin',
  user: { id: 'provider-review-user', is_superuser: true },
  getProjects: vi.fn(),
  getSubscription: vi.fn(),
  getLibrary: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    BrowserRouter: ({ children }: { children: ReactNode }) => (
      <actual.MemoryRouter initialEntries={[state.path]}>{children}</actual.MemoryRouter>
    ),
  };
});
vi.mock('../contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  AuthIdentityQueryBoundary: ({ children }: { children: ReactNode }) => children,
  useAuth: () => ({ user: state.user, loading: false }),
}));
vi.mock('../components/SiteBoundary', () => ({
  SiteBoundary: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('../providers/SEOProvider', () => ({
  SEOProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('../components/Helmet', () => ({ SEOHelmet: () => null }));
vi.mock('../components/RouteChangeTracker', () => ({ RouteChangeTracker: () => null }));
vi.mock('../components/Toast', () => ({ ToastContainer: () => null }));
vi.mock('../components/Layout', () => ({ Layout: () => null }));
vi.mock('../components/sidebar/Sidebar', () => ({ Sidebar: () => null }));
vi.mock('../components/Editor', () => ({ Editor: () => null }));
vi.mock('../components/ChatPanel', () => ({ ChatPanel: () => null }));
vi.mock('../lib/api', () => ({
  projectApi: { getAll: state.getProjects },
  fileApi: { get: vi.fn() },
}));
vi.mock('../lib/subscriptionApi', async () => {
  const actual = await vi.importActual<typeof import('../lib/subscriptionApi')>('../lib/subscriptionApi');
  return { ...actual, subscriptionApi: { getStatus: state.getSubscription } };
});
vi.mock('../lib/materialsApi', () => ({
  materialsApi: { getLibrarySummary: state.getLibrary },
}));
vi.mock('../lib/onboardingPersonaApi', async () => {
  const actual = await vi.importActual<typeof import('../lib/onboardingPersonaApi')>('../lib/onboardingPersonaApi');
  return {
    ...actual,
    onboardingPersonaApi: { getState: vi.fn().mockResolvedValue({ required: false }) },
  };
});
vi.mock('../components/admin/AdminLayout', async () => {
  const { Outlet } = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { default: () => <><div>Admin shell</div><Outlet /></> };
});
vi.mock('../pages/admin/AdminDashboard', () => ({ default: () => <div>Admin page</div> }));
vi.mock('../pages/OnboardingPersonaPage', () => ({ default: () => <div>Onboarding page</div> }));
vi.mock('../pages/Dashboard', async () => {
  const { Outlet } = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { default: () => <Outlet /> };
});
vi.mock('../pages/DashboardHome', () => ({ default: () => <div>Dashboard page</div> }));

import App from '../App';

function renderAt(path: string) {
  state.path = path;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

describe('route-scoped workbench providers', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.user = { id: 'provider-review-user', is_superuser: true };
    state.getProjects.mockResolvedValue([]);
    state.getSubscription.mockResolvedValue({ tier: 'pro', features: { materials_library_access: true } });
    state.getLibrary.mockResolvedValue([]);
  });

  it.each([
    ['/admin', 'Admin page'],
    ['/onboarding/persona', 'Onboarding page'],
  ])('does not initialize unused workbench data on %s', async (path, page) => {
    renderAt(path);
    expect(await screen.findByText(page)).toBeInTheDocument();
    expect(state.getProjects).toHaveBeenCalledTimes(0);
    expect(state.getSubscription).toHaveBeenCalledTimes(0);
    expect(state.getLibrary).toHaveBeenCalledTimes(0);
  });

  it('still initializes workbench data on the dashboard', async () => {
    renderAt('/dashboard');
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument();
    await waitFor(() => {
      expect(state.getProjects).toHaveBeenCalledTimes(1);
      expect(state.getSubscription).toHaveBeenCalledTimes(1);
      expect(state.getLibrary).toHaveBeenCalledTimes(1);
    });
  });

  it('keeps the real admin permission guard without initializing workbench data', async () => {
    state.user = { id: 'ordinary-user', is_superuser: false };
    renderAt('/admin');
    expect(await screen.findByRole('heading')).toBeInTheDocument();
    expect(screen.queryByText('Admin page')).not.toBeInTheDocument();
    expect(state.getProjects).not.toHaveBeenCalled();
    expect(state.getSubscription).not.toHaveBeenCalled();
    expect(state.getLibrary).not.toHaveBeenCalled();
  });
});

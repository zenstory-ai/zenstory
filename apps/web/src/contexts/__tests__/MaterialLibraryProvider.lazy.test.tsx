import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MaterialLibraryProvider,
  useMaterialLibraryContext,
} from '../MaterialLibraryContext';
import { MATERIAL_LIBRARY_SUMMARY_QUERY_KEY } from '../../hooks/useMaterialLibrary';
import { subscriptionApi } from '../../lib/subscriptionApi';
import { materialsApi } from '../../lib/materialsApi';

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getStatus: vi.fn(),
    getQuota: vi.fn(),
  },
  subscriptionQueryKeys: {
    status: () => ['subscription-status', 'user-1'] as const,
    quota: () => ['subscription-quota', 'user-1'] as const,
  },
}));

vi.mock('../../lib/materialsApi', () => ({
  materialsApi: {
    getLibrarySummary: vi.fn(),
    getPreview: vi.fn(),
  },
}));

vi.mock('../../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

function FilesTabConsumer() {
  // Simulates Editor.tsx reading context while on /dashboard or /project/:id (files tab)
  const lib = useMaterialLibraryContext();
  return (
    <div>
      <span data-testid="activated">{String(lib.isActivated)}</span>
      <span data-testid="count">{lib.libraries.length}</span>
      <span data-testid="denied">{String(lib.accessDenied)}</span>
      <button type="button" onClick={() => lib.activate()}>
        Open Materials
      </button>
    </div>
  );
}

function DeferredPaneHarness() {
  return (
    <MaterialLibraryProvider>
      <FilesTabConsumer />
    </MaterialLibraryProvider>
  );
}

describe('MaterialLibraryProvider on-demand loading', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
          gcTime: Infinity,
        },
      },
    });
    vi.mocked(subscriptionApi.getStatus).mockResolvedValue({
      tier: 'pro',
      features: { materials_library_access: true },
    } as never);
    vi.mocked(subscriptionApi.getQuota).mockResolvedValue({
      material_trial: { available: false, used: false, max_chapters: 3 },
    } as never);
    vi.mocked(materialsApi.getLibrarySummary).mockResolvedValue([
      {
        id: 1,
        title: 'Demo Novel',
        status: 'completed',
        counts: {
          characters: 3,
          worldview: 1,
          golden_fingers: 0,
          storylines: 2,
          relationships: 0,
        },
      },
    ]);
  });

  afterEach(async () => {
    await queryClient.cancelQueries();
    queryClient.clear();
  });

  it('makes zero subscription or materials API calls on /dashboard or /project/:id (files tab) until activated', async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <DeferredPaneHarness />
      </QueryClientProvider>,
    );

    // Flush microtasks/effects
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(screen.getByTestId('activated')).toHaveTextContent('false');
    expect(screen.getByTestId('count')).toHaveTextContent('0');
    expect(subscriptionApi.getStatus).not.toHaveBeenCalled();
    expect(subscriptionApi.getQuota).not.toHaveBeenCalled();
    expect(materialsApi.getLibrarySummary).not.toHaveBeenCalled();

    // Activate when user opens Materials
    fireEvent.click(screen.getByRole('button', { name: 'Open Materials' }));

    await waitFor(() => {
      expect(screen.getByTestId('count')).toHaveTextContent('1');
    });
    expect(screen.getByTestId('activated')).toHaveTextContent('true');
    expect(subscriptionApi.getStatus).toHaveBeenCalledTimes(1);
    expect(subscriptionApi.getQuota).not.toHaveBeenCalled();
    expect(materialsApi.getLibrarySummary).toHaveBeenCalledTimes(1);

    // Reactive invalidation continues working identically once activated
    vi.mocked(materialsApi.getLibrarySummary).mockResolvedValueOnce([
      {
        id: 1,
        title: 'Demo Novel',
        status: 'completed',
        counts: { characters: 3, worldview: 1, golden_fingers: 0, storylines: 2, relationships: 0 },
      },
      {
        id: 2,
        title: 'Second Novel',
        status: 'completed',
        counts: { characters: 1, worldview: 0, golden_fingers: 0, storylines: 0, relationships: 0 },
      },
    ]);
    await queryClient.invalidateQueries({ queryKey: MATERIAL_LIBRARY_SUMMARY_QUERY_KEY });

    await waitFor(() => {
      expect(screen.getByTestId('count')).toHaveTextContent('2');
    });
    expect(materialsApi.getLibrarySummary).toHaveBeenCalledTimes(2);
  });

  it('fetches quota for free users only after activation and respects accessDenied vs trial access', async () => {
    vi.mocked(subscriptionApi.getStatus).mockResolvedValue({
      tier: 'free',
      features: { materials_library_access: false },
    } as never);
    vi.mocked(subscriptionApi.getQuota).mockResolvedValue({
      material_trial: { available: true, used: false, max_chapters: 3 },
    } as never);

    render(
      <QueryClientProvider client={queryClient}>
        <DeferredPaneHarness />
      </QueryClientProvider>,
    );

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(subscriptionApi.getStatus).not.toHaveBeenCalled();
    expect(subscriptionApi.getQuota).not.toHaveBeenCalled();
    expect(materialsApi.getLibrarySummary).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Open Materials' }));

    await waitFor(() => {
      expect(screen.getByTestId('denied')).toHaveTextContent('true');
    });
    expect(subscriptionApi.getStatus).toHaveBeenCalledTimes(1);
    expect(subscriptionApi.getQuota).toHaveBeenCalledTimes(1);
    expect(materialsApi.getLibrarySummary).not.toHaveBeenCalled();
  });
});

import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useFirstWork } from '../useFirstWork';
import { firstWorkQueryKey, type FirstWorkState } from '../../lib/firstWorkApi';

const mockGet = vi.fn<() => Promise<FirstWorkState>>();

vi.mock('../../lib/firstWorkApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/firstWorkApi')>()),
  firstWorkApi: {
    get: () => mockGet(),
  },
}));

const sampleState: FirstWorkState = {
  status: 'created',
  project_id: 'project-1',
  file_id: 'file-1',
  available: true,
  static_guidance: true,
  opened_at: null,
  first_manual_write_at: null,
  first_ai_requested_at: null,
};

describe('useFirstWork caching', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(sampleState);
  });

  it('reuses cached first-work state across remounts without refetching until invalidated', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const first = renderHook(() => useFirstWork('user-1'), { wrapper });
    await waitFor(() => expect(first.result.current.data).toEqual(sampleState));
    expect(mockGet).toHaveBeenCalledTimes(1);

    // Simulates switching projects/tabs (unmounting and remounting consumer within staleTime).
    first.unmount();
    const second = renderHook(() => useFirstWork('user-1'), { wrapper });
    await waitFor(() => expect(second.result.current.data).toEqual(sampleState));
    expect(mockGet).toHaveBeenCalledTimes(1);

    // Explicit invalidation on actual progress still triggers a fresh read.
    const completedState: FirstWorkState = {
      ...sampleState,
      static_guidance: false,
      first_ai_requested_at: '2026-11-25T16:00:00Z',
    };
    mockGet.mockResolvedValueOnce(completedState);

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: firstWorkQueryKey('user-1') });
    });
    await waitFor(() => expect(second.result.current.data).toEqual(completedState));
    expect(mockGet).toHaveBeenCalledTimes(2);
  });
});

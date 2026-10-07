import { StrictMode, type ReactNode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '../../lib/i18n';
import { useWritingStats } from '../useWritingStats';
import { WordCountTrendChart } from '../../components/ProjectDashboard/WordCountTrendChart';
import type { ProjectDashboardStatsResponse } from '../../types/writingStats';

function dashboard(
  overrides?: Partial<ProjectDashboardStatsResponse>
): ProjectDashboardStatsResponse {
  return {
    project_id: 'project-123',
    project_name: 'Test Project',
    total_word_count: 10000,
    words_today: 500,
    words_this_week: 2000,
    words_this_month: 5000,
    chapter_completion: {
      total_chapters: 10,
      completed_chapters: 3,
      in_progress_chapters: 2,
      not_started_chapters: 5,
      completion_percentage: 30,
      chapter_details: [],
    },
    streak: {
      current_streak: 5,
      longest_streak: 10,
      streak_status: 'active',
      days_until_break: null,
      last_writing_date: '2025-01-15',
      streak_start_date: '2025-01-11',
      streak_recovery_count: 0,
    },
    ai_usage: {
      current: {
        total_sessions: 1,
        active_session_id: null,
        total_messages: 10,
        user_messages: 5,
        assistant_messages: 4,
        tool_messages: 1,
        estimated_tokens: 500,
        first_interaction_at: '2025-01-01T00:00:00Z',
        last_interaction_at: '2025-01-15T12:00:00Z',
      },
      today: { total: 5, user: 3, ai: 2, estimated_tokens: 100 },
      this_week: { total: 20, user: 12, ai: 8, estimated_tokens: 400 },
      this_month: { total: 50, user: 30, ai: 20, estimated_tokens: 1000 },
    },
    generated_at: '2025-01-15T12:00:00Z',
    ...overrides,
  }
}


function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
const clients: QueryClient[] = [];
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('access_token', 'same-user-access');
  localStorage.setItem('refresh_token', 'same-user-refresh');
});
afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
  vi.unstubAllGlobals();
  localStorage.clear();
});
function Host({ projectId, onRecord }: { projectId: string | undefined; onRecord: (request: Promise<unknown>) => void }) {
  const { stats, refetch, recordStats } = useWritingStats({ projectId });
  return <>
    <button onClick={() => void refetch()}>refresh statistics</button>
    <button onClick={() => onRecord(recordStats({ word_count: 1500, words_added: 500 }))}>record statistics</button>
    <output aria-label="current project">{stats?.project_id}</output>
    <WordCountTrendChart projectId={projectId} stats={stats} />
  </>;
}
function fixture(strict: boolean, pendingRecord = false, recordFailure = false, initialId: string | null = 'A') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const requests: { projectId: string; kind: string; body: string | null }[] = [];
  const post = deferred<Response>();
  let recordOutcome: Promise<unknown> | null = null;
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), 'http://offline.test').pathname;
    if (path.includes('/locales/')) return Promise.resolve(new Response('{}', { status: 200 }));
    const match = path.match(/^\/api\/v1\/projects\/(A|B)\/stats(\/word-count-trend|\/record)?$/);
    if (!match) throw new Error('Unexpected offline path ' + path);
    const projectId = match[1]!;
    const kind = match[2] === '/record' ? 'record' : match[2] ? 'trend' : 'stats';
    requests.push({ projectId, kind, body: typeof init?.body === 'string' ? init.body : null });
    if (kind === 'record') {
      if (recordFailure) return Promise.resolve(new Response('{"detail":"offline record failure"}', { status: 500 }));
      if (pendingRecord) return post.promise;
      return Promise.resolve(recordResponse(projectId));
    }
    const data = kind === 'stats' ? dashboard({ project_id: projectId, project_name: projectId }) : {
      project_id: projectId, period: 'daily', data: [{ date: '2026-10-06', word_count: 1500, net_words: 500 }],
    };
    return Promise.resolve(new Response(JSON.stringify(data), { status: 200 }));
  }));
  const wrap = (node: ReactNode) => <QueryClientProvider client={client}>{strict ? <StrictMode>{node}</StrictMode> : node}</QueryClientProvider>;
  const node = (id: string | undefined) => wrap(<Host projectId={id} onRecord={request => { recordOutcome = request.then(value => ({ value, error: null }), (error: unknown) => ({ value: null, error })); }} />);
  const view = render(node(initialId ?? undefined));
  const count = (id: string, kind: string) => requests.filter(r => r.projectId === id && r.kind === kind).length;
  const ready = async (id: string) => {
    await waitFor(() => expect(screen.getByLabelText('current project')).toHaveTextContent(id));
    await waitFor(() => expect(client.getQueryCache().findAll({ queryKey: ['writing-stats-word-trend', id] })[0]?.state.data).toBeDefined());
  };
  return { client, post, view, node, count, ready, requests, outcome: () => recordOutcome };
}
function recordResponse(projectId: string) {
  return new Response(JSON.stringify({ id: 'record-' + projectId, project_id: projectId, word_count: 1500, words_added: 500 }), { status: 201 });
}
for (const strict of [false, true]) describe(`actual statistics query lifecycle strict=${strict}`, () => {
  it('refreshes the active trend together with main dashboard statistics', async () => {
    const f = fixture(strict);
    await f.ready('A');
    expect(f.count('A', 'stats')).toBe(1);
    expect(f.count('A', 'trend')).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'refresh statistics' }));
    await waitFor(() => expect(f.count('A', 'stats')).toBe(2));
    console.info('ACTUAL_STATS_REFRESH', JSON.stringify(f.requests));
    await waitFor(() => expect(f.count('A', 'trend')).toBe(2));
  });
  it('invalidates both active series after a successful current-project record', async () => {
    const f = fixture(strict);
    await f.ready('A');
    fireEvent.click(screen.getByRole('button', { name: 'record statistics' }));
    await act(async () => { await f.outcome(); });
    await waitFor(() => expect(f.count('A', 'stats')).toBe(2));
    expect(f.count('A', 'record')).toBe(1);
    expect(JSON.parse(f.requests.find(r => r.kind === 'record')!.body!)).toMatchObject({ word_count: 1500, words_added: 500 });
    console.info('ACTUAL_STATS_RECORD', JSON.stringify(f.requests));
    await waitFor(() => expect(f.count('A', 'trend')).toBe(2));
  });
  it('preserves record rejection without invalidating either active series', async () => {
    const f = fixture(strict, false, true);
    await f.ready('A');
    const invalidate = vi.spyOn(f.client, 'invalidateQueries'); // calls through the actual client
    fireEvent.click(screen.getByRole('button', { name: 'record statistics' }));
    let outcome: unknown;
    await act(async () => { outcome = await f.outcome(); });
    expect(outcome).toMatchObject({ value: null, error: { status: 500 } });
    expect(invalidate).not.toHaveBeenCalled();
    expect(f.count('A', 'stats')).toBe(1);
    expect(f.count('A', 'trend')).toBe(1);
    expect(screen.getByLabelText('current project')).toHaveTextContent('A');
    invalidate.mockRestore();
  });
  it('preserves undefined-project refresh without broadening trend invalidation', async () => {
    const f = fixture(strict, false, false, null);
    f.client.setQueryData(['writing-stats-word-trend', 'B', 'daily', 14], { project_id: 'B', data: [] });
    const invalidate = vi.spyOn(f.client, 'invalidateQueries'); // observe, never replace implementation
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'refresh statistics' })); });
    expect(invalidate).not.toHaveBeenCalled();
    expect(f.requests).toHaveLength(0);
    expect(f.client.getQueryState(['writing-stats-word-trend', 'B', 'daily', 14])?.isInvalidated).toBe(false);
    invalidate.mockRestore();
  });
  it('keeps pending A record invalidation scoped to A after selecting B', async () => {
    const f = fixture(strict, true);
    await f.ready('A');
    fireEvent.click(screen.getByRole('button', { name: 'record statistics' }));
    await waitFor(() => expect(f.count('A', 'record')).toBe(1));
    f.view.rerender(f.node('B'));
    await f.ready('B');
    expect(f.count('B', 'stats')).toBe(1);
    expect(f.count('B', 'trend')).toBe(1);
    await act(async () => { f.post.resolve(recordResponse('A')); await f.outcome(); });
    console.info('ACTUAL_STATS_RECORD_SWITCH', JSON.stringify(f.requests));
    expect(screen.getByLabelText('current project')).toHaveTextContent('B');
    expect(f.count('B', 'stats')).toBe(1);
    expect(f.count('B', 'trend')).toBe(1);
    expect(f.client.getQueryState(['writing-stats', 'A'])?.isInvalidated).toBe(true);
    expect(f.client.getQueryCache().findAll({ queryKey: ['writing-stats-word-trend', 'A'] })[0]?.state.isInvalidated).toBe(true);
    expect(f.count('A', 'stats')).toBe(1); // do not force inactive A reads
    expect(f.count('A', 'trend')).toBe(1);
  });
});

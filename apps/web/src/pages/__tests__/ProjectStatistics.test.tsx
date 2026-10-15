import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import ProjectDashboardPage from '../ProjectDashboardPage';
import zh from '../../../public/locales/zh/dashboard.json';
import en from '../../../public/locales/en/dashboard.json';
import type { ProjectDashboardStatsResponse } from '../../types/writingStats';

const navigate = vi.fn();
const refresh = vi.fn();
let stats: ProjectDashboardStatsResponse | null;
let loading = false;
let fetching = false;
let error: Error | null = null;
let locale: 'zh' | 'en' = 'zh';
const trendRetry = vi.fn();
let trendError: Error | null = null;
let trendLoading = false;
const useQuery = vi.fn();

vi.mock('react-router-dom', async () => ({
  ...await vi.importActual('react-router-dom'),
  useParams: () => ({ projectId: 'project-1' }),
  useNavigate: () => navigate,
}));
vi.mock('../../hooks/useWritingStats', () => ({
  useWritingStats: () => ({ stats, isLoading: loading, isFetching: fetching, error, refetch: refresh }),
}));
vi.mock('@tanstack/react-query', () => ({ useQuery: (options: unknown) => useQuery(options) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({
  i18n: { language: locale },
  t: (key: string, options?: Record<string, unknown> | string) => {
    const value = key.split('.').reduce<unknown>((node, part) =>
      node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined,
    locale === 'zh' ? zh : en);
    const fallback = typeof options === 'string' ? options : options?.defaultValue;
    return String(value ?? fallback ?? key).replace(/{{(\w+)}}/g, (_, name: string) =>
      String(typeof options === 'object' ? options[name] ?? '' : ''));
  },
}) }));

function fixture(): ProjectDashboardStatsResponse {
  return {
    project_id: 'project-1', project_name: '书名', project_type: 'screenplay',
    total_word_count: 12000, words_today: 200, ai_words_today: 800,
    words_this_week: 700, ai_words_this_week: 1800, words_this_month: 2200, ai_words_this_month: 7000,
    chapter_completion: {
      total_chapters: 4, completed_chapters: 2, in_progress_chapters: 1,
      not_started_chapters: 77, planned_total: 80, completion_percentage: 2.5,
      chapter_details: [
        { outline_id: 'outline-1', draft_id: 'draft-1', title: '第一集', status: 'complete', word_count: 1500, target_word_count: 1500, completion_percentage: 100 },
        { outline_id: 'outline-2', draft_id: null, title: '下一集细纲', status: 'not_started', word_count: 0, target_word_count: null, completion_percentage: 0 },
        { outline_id: '', draft_id: null, title: '历史记录', status: 'in_progress', word_count: 800, target_word_count: 2000, completion_percentage: 40 },
      ],
    },
    streak: { current_streak: 0, longest_streak: 9, streak_status: 'none', days_until_break: null, last_writing_date: '2026-10-10', streak_start_date: null, streak_recovery_count: 0 },
    ai_usage: {
      current: { total_sessions: 2, active_session_id: 'session-1', total_messages: 999, user_messages: 9, ai_messages: 10, tool_messages: 980, estimated_tokens: 4000 },
      today: { total: 400, user: 3, ai: 4, estimated_tokens: 1000 },
      this_week: { total: 500, user: 5, ai: 6, estimated_tokens: 2000 },
      this_month: { total: 999, user: 9, ai: 10, estimated_tokens: 4000 },
    },
    generated_at: '2026-10-15T10:00:00Z',
  };
}
function view() { return render(<MemoryRouter><ProjectDashboardPage /></MemoryRouter>); }
function expand(name: string) { fireEvent.click(screen.getByRole('button', { name, exact: true })); }
function queryOptions() { return useQuery.mock.lastCall![0] as { enabled: boolean; queryKey: unknown[] }; }

beforeEach(() => {
  vi.clearAllMocks(); stats = fixture(); loading = false; fetching = false; error = null;
  locale = 'zh'; trendError = null; trendLoading = false;
  useQuery.mockImplementation(() => ({
    data: { data: [{ date: '2026-10-14', net_words: -47 }, { date: '2026-10-15', net_words: 200 }] },
    isLoading: trendLoading, isFetching: false, error: trendError, refetch: trendRetry,
  }));
});

describe('project statistics as one factual page', () => {
  it('has one overview and no competing tabs, health scores or inferred next actions', () => {
    view();
    expect(screen.getByRole('heading', { level: 1, name: '项目统计' })).toBeInTheDocument();
    const overview = screen.getByRole('region', { name: '写作概况' });
    expect(within(overview).getByText('12,000')).toBeInTheDocument();
    expect(within(overview).getByText('1,000')).toBeInTheDocument();
    expect(within(overview).getByText('你写 200 · AI 写 800')).toBeInTheDocument();
    expect(within(overview).getByText('已写 3 集')).toBeInTheDocument();
    expect(within(overview).getByText('计划 80 集')).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(screen.queryByText(/项目状态|下一步行动|接下来可以做|连续写作小贴士|AI 使用建议|状态不错|AI 活跃/)).not.toBeInTheDocument();
    expect(screen.getByText(/北京时间/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '分集明细' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: '更多记录' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('switches the period once, without duplicating period numbers or fetching collapsed trends', () => {
    view();
    expect(queryOptions().enabled).toBe(false);
    expand('本周');
    const overview = screen.getByRole('region', { name: '写作概况' });
    expect(within(overview).getByText('2,500')).toBeInTheDocument();
    expect(within(overview).getByText('你写 700 · AI 写 1,800')).toBeInTheDocument();
    expect(screen.queryByText('1,000')).not.toBeInTheDocument();
    expect(queryOptions().enabled).toBe(false);
  });

  it.each([
    ['novel', '章', '分章明细'],
    ['short', '篇', '正文明细'],
    ['screenplay', '集', '分集明细'],
  ] as const)('names %s units and links only exact existing files', (type, unit, detailsTitle) => {
    stats!.project_type = type; view();
    expect(screen.getByText(`已写 3 ${unit}`)).toBeInTheDocument();
    expand(detailsTitle);
    const progress = screen.getByRole('progressbar');
    expect(progress).toHaveAttribute('aria-valuenow', '2.5');
    expect(screen.getByText(`77 ${unit}还没开始`)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /第一集/ })).toHaveAttribute('href', '/project/project-1?file=draft-1');
    expect(screen.getByRole('link', { name: /下一集细纲/ })).toHaveAttribute('href', '/project/project-1?file=outline-2');
    expect(screen.queryByRole('link', { name: /历史记录/ })).not.toBeInTheDocument();
    expect(screen.getByText('历史记录')).toBeInTheDocument();
    expect(screen.getAllByRole('link')).toHaveLength(2);
  });

  it('does not invent a completion percentage for unknown or old-server plans', () => {
    delete stats!.project_type; delete stats!.ai_words_today; delete stats!.ai_words_this_week; delete stats!.ai_words_this_month;
    delete stats!.chapter_completion.planned_total; stats!.chapter_completion.completion_percentage = 100;
    view();
    expect(screen.getByText('已写 3 章')).toBeInTheDocument();
    expect(screen.getByText('你写 200 · AI 写 0')).toBeInTheDocument();
    expect(screen.getByText('尚未设置计划总数')).toBeInTheDocument();
    expand('分章明细');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText(/100%|全部完成/)).not.toBeInTheDocument();
  });

  it('labels negative handwritten trends separately from AI-inclusive totals and loads them on demand', () => {
    view();
    expect(queryOptions().enabled).toBe(false);
    expand('手写字数趋势');
    expect(queryOptions().enabled).toBe(true);
    expect(queryOptions().queryKey).toEqual(['writing-stats-word-trend', 'project-1', 'daily', 14]);
    expect(screen.getByText('仅统计你在编辑器里写的净字数，不含 AI 写入')).toBeInTheDocument();
    expect(screen.getByText('-47')).toBeInTheDocument();
    expand('本月');
    expect(queryOptions().queryKey).toEqual(['writing-stats-word-trend', 'project-1', 'monthly', 365]);
    expand('手写字数趋势');
    expect(queryOptions().enabled).toBe(false);
  });

  it('has independent retry for failed trends, rather than a successful empty state', () => {
    trendError = new Error('offline'); view(); expand('手写字数趋势');
    expect(screen.getByRole('alert')).toHaveTextContent('字数趋势加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试趋势' }));
    expect(trendRetry).toHaveBeenCalledOnce();
    expect(screen.getByText('12,000')).toBeInTheDocument();
  });

  it('distinguishes a pending trend request from a successful empty series', () => {
    useQuery.mockImplementation(() => ({ data: undefined, isLoading: true, error: null }));
    const rendered = view(); expand('手写字数趋势');
    expect(screen.getByRole('status')).toHaveTextContent('正在加载趋势');
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
    useQuery.mockImplementation(() => ({ data: { data: [] }, isLoading: false, error: null }));
    rendered.rerender(<MemoryRouter><ProjectDashboardPage /></MemoryRouter>);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('preserves the writer-only streak and author-message count, not all AI/tool messages', () => {
    view(); expand('更多记录');
    expect(screen.getByText('只计算你在编辑器里亲手写满 10 字的日期，AI 写入不计')).toBeInTheDocument();
    expect(screen.getByText('只算本项目里你发给 AI 的消息。')).toBeInTheDocument();
    expect(screen.getByText('9 条消息')).toBeInTheDocument();
    expect(screen.getByText('3 条消息')).toBeInTheDocument();
    expect(screen.queryByText(/999|400 条/)).not.toBeInTheDocument();
    expect(screen.getByText(/2026.*10.*10/)).toBeInTheDocument();
  });

  it('shows unavailable author-message counts as unavailable, never as total_messages', () => {
    delete (stats!.ai_usage.current as Partial<ProjectDashboardStatsResponse['ai_usage']['current']>).user_messages;
    delete (stats!.ai_usage.today as Partial<ProjectDashboardStatsResponse['ai_usage']['today']>).user;
    view(); expand('更多记录');
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/999|400 条/)).not.toBeInTheDocument();
  });

  it('retains content while refreshing and clearly reports a failed background refresh', () => {
    fetching = true; const rendered = view();
    const button = screen.getByRole('button', { name: '刷新' });
    expect(button).toBeDisabled();
    expect(screen.getByText('12,000')).toBeInTheDocument();
    fetching = false; error = new Error('offline'); rendered.rerender(<MemoryRouter><ProjectDashboardPage /></MemoryRouter>);
    expect(screen.getByText('12,000')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('刷新失败，已保留上次数据');
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('keeps initial loading, empty and error states honest with a return action', () => {
    stats = null; loading = true; const rendered = view();
    expect(screen.getByRole('status')).toHaveTextContent('正在加载统计');
    expect(screen.queryByText('0')).not.toBeInTheDocument();
    loading = false; rendered.rerender(<MemoryRouter><ProjectDashboardPage /></MemoryRouter>);
    expect(screen.getByText('暂无统计数据')).toBeInTheDocument();
    error = new Error('offline'); rendered.rerender(<MemoryRouter><ProjectDashboardPage /></MemoryRouter>);
    expect(screen.getByRole('alert')).toHaveTextContent('加载数据统计失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(refresh).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '返回创作' }));
    expect(navigate).toHaveBeenCalledWith('/project/project-1');
  });

  it('renders the same factual structure in English', () => {
    locale = 'en'; view();
    expect(screen.getByRole('heading', { name: 'Project statistics' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Writing overview' })).toBeInTheDocument();
    expect(screen.getByText('You 200 · AI 800')).toBeInTheDocument();
  });
});

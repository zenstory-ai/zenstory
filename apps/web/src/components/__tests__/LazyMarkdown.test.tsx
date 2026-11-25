import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  gfmPlugin: vi.fn(),
  markdownRenderSpy: vi.fn(),
}));

vi.mock('remark-gfm', () => ({
  default: mocks.gfmPlugin,
}));

vi.mock('react-markdown', () => ({
  default: ({ children, remarkPlugins }: { children: string; remarkPlugins?: unknown[] }) => {
    mocks.markdownRenderSpy(children, remarkPlugins);
    return <div data-testid="mock-markdown">{children}</div>;
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe('LazyMarkdown', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  it('caches remark-gfm at module scope and avoids double-rendering subsequent bubbles', async () => {
    const { LazyMarkdown } = await import('../LazyMarkdown');

    const { rerender } = render(<LazyMarkdown>{'First **bubble**'}</LazyMarkdown>);

    await waitFor(() => {
      expect(screen.getByText('First **bubble**')).toBeInTheDocument();
    });

    // First mount loads react-markdown and remark-gfm in parallel so remark-gfm
    // is already available on the first committed ReactMarkdown render.
    expect(mocks.markdownRenderSpy).toHaveBeenCalledTimes(1);
    const firstPlugins = mocks.markdownRenderSpy.mock.calls[0]?.[1] as unknown[];
    expect(firstPlugins[0]).toBe(mocks.gfmPlugin);

    // Re-rendering with the same content keeps the exact same memoized remarkPlugins reference.
    rerender(<LazyMarkdown>{'First **bubble**'}</LazyMarkdown>);
    expect(mocks.markdownRenderSpy).toHaveBeenCalledTimes(2);
    expect(mocks.markdownRenderSpy.mock.calls[1]?.[1]).toBe(firstPlugins);

    // Mounting a second bubble reuses the cached plugin synchronously (1 render, not 2).
    mocks.markdownRenderSpy.mockClear();
    render(<LazyMarkdown>{'Second bubble'}</LazyMarkdown>);

    await waitFor(() => {
      expect(screen.getByText('Second bubble')).toBeInTheDocument();
    });
    expect(mocks.markdownRenderSpy).toHaveBeenCalledTimes(1);
    const secondPlugins = mocks.markdownRenderSpy.mock.calls[0]?.[1] as unknown[];
    expect(secondPlugins[0]).toBe(mocks.gfmPlugin);
  });
});

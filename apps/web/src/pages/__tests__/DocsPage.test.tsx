import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DocsPage,
  MarkdownComponents,
  extractDocPath,
  findDocItem,
  getDocsDirectory,
  isExternalHref,
  loadMarkdownContent,
  normalizeDocsHref,
  resolveCategoryToFirstDoc,
} from '../DocsPage'

const mockNavigate = vi.fn()
let mockPathname = '/docs/getting-started/quick-start'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useLocation: () => ({ pathname: mockPathname }),
    useNavigate: () => mockNavigate,
    Link: ({ to, children }: { to: string; children?: React.ReactNode }) => <a href={to}>{children}</a>,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) =>
      (
        {
          loading: 'Loading...',
          menu: 'Menu',
          notFound: 'Document Not Found',
          loadError: 'Failed to Load Document',
          notFoundDesc: 'Missing doc description',
          backToHome: 'Back to Documentation Home',
        } as Record<string, string>
      )[key] ?? fallback ?? key,
    i18n: {
      language: 'en',
    },
  }),
}))

vi.mock('../../components/PublicHeader', () => ({
  PublicHeader: () => <div data-testid="public-header">PublicHeader</div>,
}))

vi.mock('../../components/docs/DocsSidebar', () => ({
  DocsSidebar: ({ isOpen }: { isOpen: boolean }) => <div data-testid="docs-sidebar">{String(isOpen)}</div>,
}))

vi.mock('../../components/LazyMarkdown', () => ({
  LazyMarkdown: ({ children }: { children?: string }) => <div data-testid="lazy-markdown">{children}</div>,
}))

vi.mock('../../lib/logger', () => ({
  logger: {
    error: vi.fn(),
  },
}))

describe('DocsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPathname = '/docs/getting-started/quick-start'
    vi.stubGlobal('scrollTo', vi.fn())
  })

  it('loads and renders markdown content for an existing docs route', async () => {
    render(<DocsPage />)

    expect(screen.getByTestId('public-header')).toBeInTheDocument()
    expect(await screen.findByTestId('lazy-markdown')).toHaveTextContent('Quick Start')
  })

  it('redirects category routes to the first child document', async () => {
    mockPathname = '/docs/getting-started'

    render(<DocsPage />)

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/docs/getting-started/quick-start', { replace: true })
    })
  })

  it('shows the not-found state for missing docs', async () => {
    mockPathname = '/docs/not-real'

    render(<DocsPage />)

    expect(await screen.findByText('Document Not Found')).toBeInTheDocument()
    expect(screen.getByText('Back to Documentation Home')).toBeInTheDocument()
  })

  it('exports the docs path helpers for internal link normalization', async () => {
    expect(extractDocPath('/docs/getting-started/quick-start.md')).toBe('getting-started/quick-start')
    expect(extractDocPath('/outside-docs')).toBe('')
    expect(isExternalHref('https://zenstory.ai')).toBe(true)
    expect(isExternalHref('tel:+8613800138000')).toBe(true)
    expect(getDocsDirectory('/docs/getting-started/quick-start')).toBe('/docs/getting-started/')
    expect(getDocsDirectory('/docs')).toBe('/docs/')
    expect(getDocsDirectory('quick-start')).toBe('/docs/')
    expect(normalizeDocsHref('installation.md', '/docs/getting-started/quick-start')).toBe('/docs/getting-started/installation')
    expect(normalizeDocsHref('installation.md?step=1#finish', '/docs/getting-started/quick-start')).toBe(
      '/docs/getting-started/installation?step=1#finish',
    )
    expect(normalizeDocsHref('/docs/reference/faq', '/docs/getting-started/quick-start')).toBe('/docs/reference/faq')
    expect(normalizeDocsHref('/getting-started/quick-start.md?step=1#intro', '/docs/reference/faq')).toBe(
      '/docs/getting-started/quick-start?step=1#intro',
    )
    expect(normalizeDocsHref('/not-docs/guide', '/docs/getting-started/quick-start')).toBeNull()
    expect(normalizeDocsHref('#section', '/docs/getting-started/quick-start')).toBeNull()
    expect(resolveCategoryToFirstDoc('/docs/getting-started')).toBe('/docs/getting-started/quick-start')
    expect(resolveCategoryToFirstDoc('/docs/reference/faq')).toBeNull()
    expect(findDocItem('/docs/reference/faq')?.title).toBe('FAQ')
    expect(await loadMarkdownContent('', 'zh')).toContain('ZenStory 工作台帮助文档')
    expect(await loadMarkdownContent('', 'en')).toContain('ZenStory Workbench Documentation')
    expect(await loadMarkdownContent('getting-started/quick-start', 'en')).toContain('Quick Start')
    expect(await loadMarkdownContent('reference/faq', 'zh')).toContain('FAQ')
    expect(await loadMarkdownContent('missing/doc', 'zh')).toBeNull()
  })

  it('renders internal and external markdown links with the correct targets', () => {
    const components = MarkdownComponents('/docs/getting-started/quick-start')
    const InternalLink = components.a

    const { rerender } = render(<InternalLink href="installation.md">Install</InternalLink>)
    expect(screen.getByRole('link', { name: 'Install' })).toHaveAttribute('href', '/docs/getting-started/installation')

    rerender(<InternalLink href="https://example.com">External</InternalLink>)
    expect(screen.getByRole('link', { name: 'External' })).toHaveAttribute('href', 'https://example.com')
    expect(screen.getByRole('link', { name: 'External' })).toHaveAttribute('target', '_blank')

    rerender(<InternalLink href="mailto:team@zenstory.ai">Email</InternalLink>)
    expect(screen.getByRole('link', { name: 'Email' })).toHaveAttribute('href', 'mailto:team@zenstory.ai')
    expect(screen.getByRole('link', { name: 'Email' })).not.toHaveAttribute('target')
  })

  it('renders the markdown component helpers for headings, lists, code, tables, and media', () => {
    const components = MarkdownComponents('/docs/getting-started/quick-start')

    const {
      h1: H1,
      h2: H2,
      h3: H3,
      h4: H4,
      p: Paragraph,
      ul: UnorderedList,
      ol: OrderedList,
      li: ListItem,
      blockquote: BlockQuote,
      code: Code,
      pre: Pre,
      table: Table,
      thead: TableHead,
      th: TableHeader,
      td: TableCell,
      hr: HorizontalRule,
      img: Image,
    } = components

    render(
      <>
        <H1>Heading 1</H1>
        <H2>Heading 2</H2>
        <H3>Heading 3</H3>
        <H4>Heading 4</H4>
        <Paragraph>Body text</Paragraph>
        <UnorderedList>
          <ListItem>Unordered item</ListItem>
        </UnorderedList>
        <OrderedList>
          <ListItem>Ordered item</ListItem>
        </OrderedList>
        <BlockQuote>Quoted text</BlockQuote>
        <Code inline={true}>inline code</Code>
        <Pre>preformatted</Pre>
        <Table>
          <TableHead>
            <tr>
              <TableHeader>Header</TableHeader>
            </tr>
          </TableHead>
          <tbody>
            <tr>
              <TableCell>Cell</TableCell>
            </tr>
          </tbody>
        </Table>
        <HorizontalRule />
        <Image src="/docs/example.png" alt="Example image" />
      </>,
    )

    expect(screen.getByRole('heading', { name: 'Heading 1' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Heading 2' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Heading 3' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Heading 4' })).toBeInTheDocument()
    expect(screen.getByText('Body text')).toBeInTheDocument()
    expect(screen.getByText('Unordered item')).toBeInTheDocument()
    expect(screen.getByText('Ordered item')).toBeInTheDocument()
    expect(screen.getByText('Quoted text')).toBeInTheDocument()
    expect(screen.getByText('inline code')).toBeInTheDocument()
    expect(screen.getByText('preformatted')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('Header')).toBeInTheDocument()
    expect(screen.getByText('Cell')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Example image' })).toHaveAttribute('src', '/docs/example.png')
  })

  it('opens the mobile docs sidebar from the menu button', async () => {
    render(<DocsPage />)

    expect(screen.getByTestId('docs-sidebar')).toHaveTextContent('false')
    const menuButton = screen.getByRole('button', { name: /Menu/i })
    expect(menuButton).toBeInTheDocument()

    fireEvent.click(menuButton)

    await waitFor(() => {
      expect(screen.getByTestId('docs-sidebar')).toHaveTextContent('true')
    })
  })
})

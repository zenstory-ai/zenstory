import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LoadingSpinner, PageLoader, InlineLoader } from '../LoadingSpinner'

// Resolve only real keys (default namespace is `common`), so a wrong key path
// shows up as the raw key instead of being masked by the inline fallback.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => ({ 'common:loading': '加载中...' } as Record<string, string>)[key] ?? key,
  }),
}))

// Mock lucide-react
vi.mock('lucide-react', () => ({
  Loader2: vi.fn(({ size, className }) => (
    <svg
      data-testid="loader-icon"
      data-size={size}
      className={className}
      viewBox="0 0 24 24"
    >
      <circle cx="12" cy="12" r="10" />
    </svg>
  )),
}))

describe('LoadingSpinner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe('Basic Rendering', () => {
    it('renders with default props', () => {
      const { container } = render(<LoadingSpinner />)
      expect(container.firstChild).toBeInTheDocument()
    })

    it('renders icon variant by default', () => {
      render(<LoadingSpinner />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon).toBeInTheDocument()
    })

    it('renders icon variant when specified', () => {
      render(<LoadingSpinner variant="icon" />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon).toBeInTheDocument()
    })

    it('renders CSS variant when specified', () => {
      const { container } = render(<LoadingSpinner variant="css" />)
      // CSS variant renders a div with border classes
      const spinnerDiv = container.querySelector('.rounded-full.animate-spin')
      expect(spinnerDiv).toBeInTheDocument()
      // Icon should not be present
      expect(screen.queryByTestId('loader-icon')).not.toBeInTheDocument()
    })
  })

  describe('Sizes', () => {
    it('applies correct icon size for xs', () => {
      render(<LoadingSpinner size="xs" />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon).toHaveAttribute('data-size', '12')
    })

    it('applies correct icon size for sm', () => {
      render(<LoadingSpinner size="sm" />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon).toHaveAttribute('data-size', '16')
    })

    it('applies correct icon size for md (default)', () => {
      render(<LoadingSpinner size="md" />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon).toHaveAttribute('data-size', '20')
    })

    it('applies correct icon size for lg', () => {
      render(<LoadingSpinner size="lg" />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon).toHaveAttribute('data-size', '32')
    })

    it('applies correct icon size for xl', () => {
      render(<LoadingSpinner size="xl" />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon).toHaveAttribute('data-size', '48')
    })

    it('applies correct CSS classes for CSS variant sizes', () => {
      const { container } = render(<LoadingSpinner variant="css" size="lg" />)
      const spinner = container.querySelector('.w-8.h-8')
      expect(spinner).toBeInTheDocument()
    })

    it('applies correct CSS classes for xl size', () => {
      const { container } = render(<LoadingSpinner variant="css" size="xl" />)
      const spinner = container.querySelector('.w-12.h-12')
      expect(spinner).toBeInTheDocument()
    })
  })

  describe('Colors', () => {
    it('applies primary color by default', () => {
      render(<LoadingSpinner />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon.className).toContain('text-[hsl(var(--accent-primary))]')
    })

    it('applies white color when specified', () => {
      render(<LoadingSpinner color="white" />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon.className).toContain('text-white')
    })

    it('applies secondary color when specified', () => {
      render(<LoadingSpinner color="secondary" />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon.className).toContain('text-[hsl(var(--text-secondary))]')
    })

    it('applies correct border colors for CSS variant primary', () => {
      const { container } = render(<LoadingSpinner variant="css" color="primary" />)
      const spinner = container.querySelector('.rounded-full.animate-spin')
      expect(spinner?.className).toContain('border-[hsl(var(--border-color))]')
      expect(spinner?.className).toContain('border-t-[hsl(var(--accent-primary))]')
    })

    it('applies correct border colors for CSS variant white', () => {
      const { container } = render(<LoadingSpinner variant="css" color="white" />)
      const spinner = container.querySelector('.rounded-full.animate-spin')
      expect(spinner?.className).toContain('border-white/30')
      expect(spinner?.className).toContain('border-t-white')
    })

    it('applies correct border colors for CSS variant secondary', () => {
      const { container } = render(<LoadingSpinner variant="css" color="secondary" />)
      const spinner = container.querySelector('.rounded-full.animate-spin')
      // Secondary color uses border color for base and text-secondary for top
      expect(spinner?.className).toContain('border-t-[hsl(var(--text-secondary))]')
    })
  })

  describe('Labels', () => {
    it('renders label when provided', () => {
      render(<LoadingSpinner label="Loading..." />)
      expect(screen.getByText('Loading...')).toBeInTheDocument()
    })

    it('renders spinner and label in horizontal layout by default', () => {
      const { container } = render(<LoadingSpinner label="Loading..." />)
      const wrapper = container.querySelector('.flex.items-center.justify-center.gap-2')
      expect(wrapper).toBeInTheDocument()
    })

    it('renders spinner and label in vertical layout when vertical is true', () => {
      const { container } = render(<LoadingSpinner label="Loading..." vertical />)
      const wrapper = container.querySelector('.flex.flex-col.items-center.justify-center.gap-4')
      expect(wrapper).toBeInTheDocument()
    })

    it('applies text-sm class to label in vertical layout', () => {
      render(<LoadingSpinner label="Loading..." vertical />)
      const label = screen.getByText('Loading...')
      expect(label.className).toContain('text-sm')
    })

    it('applies text-sm class to label in horizontal layout', () => {
      render(<LoadingSpinner label="Loading..." />)
      const label = screen.getByText('Loading...')
      expect(label.className).toContain('text-sm')
    })

    it('renders without label container when no label provided', () => {
      const { container } = render(<LoadingSpinner />)
      // Should just be a span with the spinner, no flex container
      const flexContainer = container.querySelector('.flex')
      expect(flexContainer).not.toBeInTheDocument()
    })
  })

  describe('Custom ClassName', () => {
    it('applies custom className to container', () => {
      const { container } = render(<LoadingSpinner className="my-custom-class" />)
      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain('my-custom-class')
    })

    it('applies custom className with label', () => {
      const { container } = render(<LoadingSpinner label="Loading..." className="custom-class" />)
      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain('custom-class')
    })
  })

  describe('Animation', () => {
    it('applies animate-spin class to icon variant', () => {
      render(<LoadingSpinner variant="icon" />)
      const icon = screen.getByTestId('loader-icon')
      expect(icon.className).toContain('animate-spin')
    })

    it('applies animate-spin class to CSS variant', () => {
      const { container } = render(<LoadingSpinner variant="css" />)
      const spinner = container.querySelector('.animate-spin')
      expect(spinner).toBeInTheDocument()
    })
  })

  describe('CSS Variant Specifics', () => {
    it('renders with border-2 class', () => {
      const { container } = render(<LoadingSpinner variant="css" />)
      const spinner = container.querySelector('.border-2')
      expect(spinner).toBeInTheDocument()
    })

    it('renders with rounded-full class', () => {
      const { container } = render(<LoadingSpinner variant="css" />)
      const spinner = container.querySelector('.rounded-full')
      expect(spinner).toBeInTheDocument()
    })

    it('applies xs size classes correctly', () => {
      const { container } = render(<LoadingSpinner variant="css" size="xs" />)
      const spinner = container.querySelector('.w-3.h-3')
      expect(spinner).toBeInTheDocument()
    })

    it('applies sm size classes correctly', () => {
      const { container } = render(<LoadingSpinner variant="css" size="sm" />)
      const spinner = container.querySelector('.w-4.h-4')
      expect(spinner).toBeInTheDocument()
    })

    it('applies md size classes correctly', () => {
      const { container } = render(<LoadingSpinner variant="css" size="md" />)
      const spinner = container.querySelector('.w-5.h-5')
      expect(spinner).toBeInTheDocument()
    })
  })
})

describe('PageLoader', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders with default label', () => {
    render(<PageLoader />)
    expect(screen.getByText('加载中...')).toBeInTheDocument()
  })

  it('renders with custom label', () => {
    render(<PageLoader label="Loading your workspace..." />)
    expect(screen.getByText('Loading your workspace...')).toBeInTheDocument()
  })

  it('centers content on page', () => {
    const { container } = render(<PageLoader />)
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toContain('flex')
    expect(wrapper.className).toContain('items-center')
    expect(wrapper.className).toContain('justify-center')
  })

  it('has min-h-screen class', () => {
    const { container } = render(<PageLoader />)
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toContain('min-h-screen')
  })

  it('uses large spinner size', () => {
    render(<PageLoader />)
    const icon = screen.getByTestId('loader-icon')
    expect(icon).toHaveAttribute('data-size', '32')
  })

  it('uses vertical layout', () => {
    const { container } = render(<PageLoader label="Loading..." />)
    const flexContainer = container.querySelector('.flex-col')
    expect(flexContainer).toBeInTheDocument()
  })

  it('applies background color class', () => {
    const { container } = render(<PageLoader />)
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toContain('bg-[hsl(var(--bg-primary))]')
  })
})

describe('InlineLoader', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders with default label', () => {
    render(<InlineLoader />)
    expect(screen.getByText('加载中...')).toBeInTheDocument()
  })

  it('renders with custom label', () => {
    render(<InlineLoader label="Fetching data..." />)
    expect(screen.getByText('Fetching data...')).toBeInTheDocument()
  })

  it('uses small spinner size', () => {
    render(<InlineLoader />)
    const icon = screen.getByTestId('loader-icon')
    expect(icon).toHaveAttribute('data-size', '16')
  })

  it('applies horizontal layout with items-center', () => {
    const { container } = render(<InlineLoader label="Loading..." />)
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toContain('flex')
    expect(wrapper.className).toContain('items-center')
  })

  it('applies padding classes', () => {
    const { container } = render(<InlineLoader />)
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toContain('py-8')
  })

  it('applies gap-2 class', () => {
    const { container } = render(<InlineLoader />)
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toContain('gap-2')
  })
})

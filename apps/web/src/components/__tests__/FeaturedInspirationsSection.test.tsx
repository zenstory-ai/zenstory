import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FeaturedInspirationsSection } from '../inspirations/FeaturedInspirationsSection'

const mockUseFeaturedInspirations = vi.fn()

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('../../hooks/useInspirations', () => ({
  useFeaturedInspirations: (limit?: number) => mockUseFeaturedInspirations(limit),
}))

describe('FeaturedInspirationsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows an error alert with retry instead of fake empty state when loading fails', () => {
    const refetch = vi.fn()
    mockUseFeaturedInspirations.mockReturnValue({
      featured: [],
      isLoading: false,
      isFetching: false,
      error: new Error('network failed'),
      refetch,
    })

    render(
      <MemoryRouter>
        <FeaturedInspirationsSection isMobile={false} isTablet={false} />
      </MemoryRouter>
    )

    expect(screen.getByRole('alert')).toHaveTextContent('inspirations.loadFailed')
    expect(screen.queryByText('inspirations.emptyTitle')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'common:retry' }))
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('shows empty state only when there is no error and featured list is empty', () => {
    mockUseFeaturedInspirations.mockReturnValue({
      featured: [],
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    })

    render(
      <MemoryRouter>
        <FeaturedInspirationsSection isMobile={false} isTablet={false} />
      </MemoryRouter>
    )

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('inspirations.emptyTitle')).toBeInTheDocument()
  })
})

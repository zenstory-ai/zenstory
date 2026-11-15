import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PageLoader } from '../PageLoader'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      (
        {
          'common:loading': 'Loading…',
        } as Record<string, string>
      )[key] ?? key,
  }),
}))

describe('PageLoader', () => {
  it('renders the full page loading state', () => {
    render(<PageLoader />)
    expect(screen.getByText('Loading…')).toBeInTheDocument()
  })
})

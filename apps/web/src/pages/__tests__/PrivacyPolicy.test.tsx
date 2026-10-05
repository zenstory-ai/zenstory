import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PrivacyPolicy from '../PrivacyPolicy'
import zhPrivacy from '../../../public/locales/zh/privacy.json'
import enPrivacy from '../../../public/locales/en/privacy.json'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { returnObjects?: boolean }) => {
      if (options?.returnObjects) {
        return ['Item A', 'Item B']
      }
      return (
        {
          title: 'Privacy Policy',
          lastUpdated: 'Updated recently',
          'sections.introduction.title': 'Introduction',
          'sections.introduction.content': 'Intro content',
          'sections.dataCollection.title': 'Data collection',
          'sections.dataCollection.content': 'Collection content',
          'sections.dataCollection.subsections.personalInfo.title': 'Personal info',
          'sections.dataCollection.subsections.content.title': 'Content data',
          'sections.dataCollection.subsections.usage.title': 'Usage data',
          'sections.dataCollection.subsections.technical.title': 'Technical data',
          'sections.dataUsage.title': 'Data usage',
          'sections.dataUsage.content': 'Usage content',
          'sections.dataSharing.title': 'Data sharing',
          'sections.dataSharing.content': 'Sharing content',
          'sections.dataSharing.subsections.serviceProviders.title': 'Service providers',
          'sections.dataSharing.subsections.serviceProviders.content': 'Provider content',
          'sections.dataSharing.subsections.analytics.title': 'Analytics',
          'sections.dataSharing.subsections.analytics.content': 'Analytics content',
          'sections.dataSharing.subsections.aiTraining.title': 'AI training',
          'sections.dataSharing.subsections.aiTraining.content': 'AI training content',
          'sections.dataSharing.subsections.legal.title': 'Legal',
          'sections.dataSharing.subsections.legal.content': 'Legal content',
          'sections.dataSharing.subsections.businessTransfer.title': 'Business transfer',
          'sections.dataSharing.subsections.businessTransfer.content': 'Business content',
          'sections.dataSharing.subsections.userConsent.title': 'Consent',
          'sections.dataSharing.subsections.userConsent.content': 'Consent content',
          'sections.dataSecurity.title': 'Data security',
          'sections.dataSecurity.content': 'Security content',
          'sections.dataRetention.title': 'Data retention',
          'sections.dataRetention.content': 'Retention content',
          'sections.userRights.title': 'User rights',
          'sections.userRights.content': 'Rights content',
          'sections.cookies.title': 'Cookies',
          'sections.cookies.content': 'Cookie content',
          'sections.children.title': 'Children',
          'sections.children.content': 'Children content',
          'sections.international.title': 'International transfers',
          'sections.international.content': 'International content',
          'sections.changes.title': 'Changes',
          'sections.changes.content': 'Changes content',
          'sections.contact.title': 'Contact',
          'sections.contact.content': 'Contact content',
        } as Record<string, string>
      )[key] ?? key
    },
  }),
}))

vi.mock('../../components/PublicHeader', () => ({
  PublicHeader: () => <div>PublicHeader</div>,
}))

describe('PrivacyPolicy', () => {
  beforeEach(() => {
    vi.stubGlobal('scrollTo', vi.fn())
  })

  it('renders key privacy sections and scrolls to the top on load', () => {
    render(<PrivacyPolicy />)

    expect(screen.getByText('Privacy Policy')).toBeInTheDocument()
    expect(screen.getByText('Introduction')).toBeInTheDocument()
    expect(screen.getByText('Data security')).toBeInTheDocument()
    expect(screen.getByText('Contact')).toBeInTheDocument()
    expect(screen.getByText('Analytics')).toBeInTheDocument()
    expect(screen.getByText('Analytics content')).toBeInTheDocument()
    expect(screen.getAllByText('Item A').length).toBeGreaterThan(0)
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0)
  })
})

describe('privacy policy locales', () => {
  it.each([
    ['zh', zhPrivacy, '2026年10月5日'],
    ['en', enPrivacy, 'October 5, 2026'],
  ])('%s discloses PostHog analytics factually', (_lang, privacy, date) => {
    const analytics = privacy.sections.dataSharing.subsections.analytics
    expect(privacy.lastUpdated).toContain(date)
    expect(analytics.content).toContain('PostHog')
    expect(analytics.content).toContain('ph_')
    expect(analytics.content).toMatch(/localStorage/)
    expect(analytics.content).toMatch(/美国|United States/)
  })
})


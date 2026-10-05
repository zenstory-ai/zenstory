import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { PublicHeader } from '../components/PublicHeader';

export default function PrivacyPolicy() {
  const { t } = useTranslation('privacy');

  // 页面加载时滚动到顶部
  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col">
      <PublicHeader variant="home" maxWidth="max-w-6xl" />
      
      <main className="flex-1 max-w-4xl mx-auto px-4 py-12">
        <div className="bg-[hsl(var(--bg-secondary))] rounded-2xl shadow-xl p-8 md:p-12">
          {/* Header */}
          <div className="mb-12 pb-6 border-b border-[hsl(var(--border-color))]">
            <h1 className="text-4xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('title')}
            </h1>
            <p className="text-[hsl(var(--text-secondary))] text-sm">
              {t('lastUpdated')}
            </p>
          </div>

          {/* Introduction */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.introduction.title')}
            </h2>
            <div className="pl-4">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed">
                {t('sections.introduction.content')}
              </p>
            </div>
          </section>

          {/* Data Collection */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.dataCollection.title')}
            </h2>
            <div className="pl-4 space-y-6">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed">
                {t('sections.dataCollection.content')}
              </p>
              
              <div className="grid gap-4">
                {/* Personal Info */}
                <div className="bg-[hsl(var(--bg-tertiary))] rounded-xl p-5">
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-3">
                    {t('sections.dataCollection.subsections.personalInfo.title')}
                  </h3>
                  <ul className="space-y-2">
                    {(t('sections.dataCollection.subsections.personalInfo.items', { returnObjects: true }) as string[]).map((item: string, index: number) => (
                      <li key={index} className="flex items-start gap-2 text-[hsl(var(--text-primary))] text-sm">
                        <span className="text-[hsl(var(--text-secondary))] mt-1">•</span>
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                {/* Content Data */}
                <div className="bg-[hsl(var(--bg-tertiary))] rounded-xl p-5">
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-3">
                    {t('sections.dataCollection.subsections.content.title')}
                  </h3>
                  <ul className="space-y-2">
                    {(t('sections.dataCollection.subsections.content.items', { returnObjects: true }) as string[]).map((item: string, index: number) => (
                      <li key={index} className="flex items-start gap-2 text-[hsl(var(--text-primary))] text-sm">
                        <span className="text-[hsl(var(--text-secondary))] mt-1">•</span>
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                {/* Usage Data */}
                <div className="bg-[hsl(var(--bg-tertiary))] rounded-xl p-5">
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-3">
                    {t('sections.dataCollection.subsections.usage.title')}
                  </h3>
                  <ul className="space-y-2">
                    {(t('sections.dataCollection.subsections.usage.items', { returnObjects: true }) as string[]).map((item: string, index: number) => (
                      <li key={index} className="flex items-start gap-2 text-[hsl(var(--text-primary))] text-sm">
                        <span className="text-[hsl(var(--text-secondary))] mt-1">•</span>
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                {/* Technical Data */}
                <div className="bg-[hsl(var(--bg-tertiary))] rounded-xl p-5">
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-3">
                    {t('sections.dataCollection.subsections.technical.title')}
                  </h3>
                  <ul className="space-y-2">
                    {(t('sections.dataCollection.subsections.technical.items', { returnObjects: true }) as string[]).map((item: string, index: number) => (
                      <li key={index} className="flex items-start gap-2 text-[hsl(var(--text-primary))] text-sm">
                        <span className="text-[hsl(var(--text-secondary))] mt-1">•</span>
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </section>

          {/* Data Usage */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.dataUsage.title')}
            </h2>
            <div className="pl-4">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed mb-4">
                {t('sections.dataUsage.content')}
              </p>
              <ul className="space-y-2">
                {(t('sections.dataUsage.items', { returnObjects: true }) as string[]).map((item: string, index: number) => (
                  <li key={index} className="flex items-start gap-2 text-[hsl(var(--text-primary))]">
                    <span className="text-[hsl(var(--text-secondary))] mt-1">✓</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          {/* Data Sharing */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.dataSharing.title')}
            </h2>
            <div className="pl-4 space-y-6">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed">
                {t('sections.dataSharing.content')}
              </p>
              
              <div className="space-y-4">
                <div>
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-2">
                    {t('sections.dataSharing.subsections.serviceProviders.title')}
                  </h3>
                  <p className="text-[hsl(var(--text-primary))] leading-relaxed text-sm">
                    {t('sections.dataSharing.subsections.serviceProviders.content')}
                  </p>
                </div>

                <div>
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-2">
                    {t('sections.dataSharing.subsections.analytics.title')}
                  </h3>
                  <p className="text-[hsl(var(--text-primary))] leading-relaxed text-sm">
                    {t('sections.dataSharing.subsections.analytics.content')}
                  </p>
                </div>

                <div>
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-2">
                    {t('sections.dataSharing.subsections.aiTraining.title')}
                  </h3>
                  <p className="text-[hsl(var(--text-primary))] leading-relaxed text-sm">
                    {t('sections.dataSharing.subsections.aiTraining.content')}
                  </p>
                </div>

                <div>
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-2">
                    {t('sections.dataSharing.subsections.legal.title')}
                  </h3>
                  <p className="text-[hsl(var(--text-primary))] leading-relaxed text-sm">
                    {t('sections.dataSharing.subsections.legal.content')}
                  </p>
                </div>

                <div>
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-2">
                    {t('sections.dataSharing.subsections.businessTransfer.title')}
                  </h3>
                  <p className="text-[hsl(var(--text-primary))] leading-relaxed text-sm">
                    {t('sections.dataSharing.subsections.businessTransfer.content')}
                  </p>
                </div>

                <div>
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-2">
                    {t('sections.dataSharing.subsections.userConsent.title')}
                  </h3>
                  <p className="text-[hsl(var(--text-primary))] leading-relaxed text-sm">
                    {t('sections.dataSharing.subsections.userConsent.content')}
                  </p>
                </div>
              </div>
            </div>
          </section>

          {/* Data Security */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.dataSecurity.title')}
            </h2>
            <div className="pl-4">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed mb-4">
                {t('sections.dataSecurity.content')}
              </p>
              <div className="grid md:grid-cols-2 gap-3">
                {(t('sections.dataSecurity.measures', { returnObjects: true }) as string[]).map((item: string, index: number) => (
                  <div key={index} className="bg-[hsl(var(--bg-tertiary))] rounded-lg p-4 flex items-start gap-3">
                    <span className="flex items-center justify-center w-6 h-6 rounded bg-[hsl(var(--bg-card))] text-[hsl(var(--text-secondary))] text-sm font-bold flex-shrink-0">
                      {index + 1}
                    </span>
                    <span className="text-[hsl(var(--text-primary))] text-sm leading-snug">{item}</span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* Data Retention */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.dataRetention.title')}
            </h2>
            <div className="pl-4">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed">
                {t('sections.dataRetention.content')}
              </p>
            </div>
          </section>

          {/* User Rights */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.userRights.title')}
            </h2>
            <div className="pl-4 space-y-6">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed">
                {t('sections.userRights.content')}
              </p>
              <ul className="space-y-2">
                {(t('sections.userRights.rights', { returnObjects: true }) as string[]).map((item: string, index: number) => (
                  <li key={index} className="flex items-start gap-2 text-[hsl(var(--text-primary))]">
                    <span className="text-[hsl(var(--text-secondary))] mt-1">✓</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
              <p className="text-[hsl(var(--text-secondary))] text-sm italic">
                {t('sections.userRights.note')}
              </p>

              {/* Data Export */}
              <div className="bg-[hsl(var(--bg-tertiary))] rounded-xl p-5">
                <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-3">
                  {t('sections.userRights.dataExport.title')}
                </h3>
                <p className="text-[hsl(var(--text-primary))] leading-relaxed text-sm">
                  {t('sections.userRights.dataExport.content')}
                </p>
              </div>
            </div>
          </section>

          {/* International Transfers */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.internationalTransfers.title')}
            </h2>
            <div className="pl-4">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed">
                {t('sections.internationalTransfers.content')}
              </p>
            </div>
          </section>

          {/* Children */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.children.title')}
            </h2>
            <div className="pl-4">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed">
                {t('sections.children.content')}
              </p>
            </div>
          </section>

          {/* Policy Changes */}
          <section className="mb-12">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.policyChanges.title')}
            </h2>
            <div className="pl-4">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed">
                {t('sections.policyChanges.content')}
              </p>
            </div>
          </section>

          {/* Contact */}
          <section className="mb-8">
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-4">
              {t('sections.contact.title')}
            </h2>
            <div className="pl-4">
              <p className="text-[hsl(var(--text-primary))] leading-relaxed mb-3">
                {t('sections.contact.content')}
              </p>
              <p className="text-[hsl(var(--text-primary))] font-medium">
                {t('sections.contact.email')}
              </p>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

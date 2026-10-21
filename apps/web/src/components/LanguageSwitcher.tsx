import { useTranslation } from 'react-i18next';
import { Globe } from 'lucide-react';

interface LanguageSwitcherProps {
  className?: string;
  variant?: 'icon' | 'full';
}

export const LanguageSwitcher = ({ className = '', variant = 'icon' }: LanguageSwitcherProps) => {
  const { i18n, t } = useTranslation(['common']);

  const toggleLanguage = () => {
    const newLang = i18n.language === 'zh' ? 'en' : 'zh';
    i18n.changeLanguage(newLang);
  };

  const currentLanguage = i18n.language === 'zh' ? t('common:language.zh') : t('common:language.en');
  const switchLabel = i18n.language === 'zh' ? t('common:language.switchToEnglish') : t('common:language.switchToChinese');
  // The visible text is the current language; a reader who cannot read it still hears what the button does.
  const accessibleLabel = `${currentLanguage} · ${switchLabel}`;

  if (variant === 'full') {
    return (
      <button
        onClick={toggleLanguage}
        aria-label={accessibleLabel}
        title={switchLabel}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-[hsl(var(--bg-tertiary))] transition-colors text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] ${className}`}
      >
        <Globe size={16} />
        <span className="text-sm">{currentLanguage}</span>
      </button>
    );
  }

  return (
    <button
      onClick={toggleLanguage}
      className={`flex min-h-11 min-w-11 md:min-h-8 md:min-w-8 items-center justify-center gap-1 md:gap-1.5 px-1.5 md:px-2 py-0.5 md:py-1 rounded-lg hover:bg-[hsl(var(--bg-tertiary))] transition-colors text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] ${className}`}
      title={switchLabel}
      aria-label={accessibleLabel}
    >
      <Globe size={14} className="w-3.5 h-3.5 md:w-4 md:h-4" />
      <span className="text-xs md:text-sm font-medium">{currentLanguage}</span>
    </button>
  );
};

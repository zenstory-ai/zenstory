import { expect, Locator, Page } from '@playwright/test';
import { BasePage } from './BasePage';

type SupportedLanguage = 'zh' | 'en';
type ThemeMode = 'dark' | 'light';

const ACCENT_PRIMARY_BY_COLOR: Record<string, string> = {
  '#4a9eff': '217 91% 43%',
  '#22c55e': '142 71% 26%',
  '#fbbf24': '38 92% 28%',
  '#f87171': '0 84% 40%',
  '#ec4899': '330 81% 40%',
  '#8b5cf6': '262 83% 43%',
};

/**
 * SettingsRegressionPOM
 *
 * Dedicated POM used by regression tests that must validate real runtime effects
 * of settings changes (DOM state + localStorage + CSS variables), not only button state.
 */
export class SettingsRegressionPOM extends BasePage {
  readonly modalOverlay: Locator;
  readonly profileTab: Locator;
  readonly generalTab: Locator;

  constructor(page: Page) {
    super(page);
    this.modalOverlay = page.locator('[role="dialog"], [role="alertdialog"]').first();
    this.profileTab = page.getByTestId('settings-tab-profile');
    this.generalTab = page.getByTestId('settings-tab-general');
  }

  private userPanelToggleButton(): Locator {
    return this.page.getByTestId('dashboard-user-panel-toggle').first();
  }

  private openSettingsActionButton(): Locator {
    return this.page.getByTestId('dashboard-open-settings-button').first();
  }

  private quickLanguageButton(language: SupportedLanguage): Locator {
    const testId = language === 'zh' ? 'dashboard-quick-language-zh' : 'dashboard-quick-language-en';
    return this.page.getByTestId(testId).first();
  }

  async openUserPanel(): Promise<void> {
    if (await this.quickLanguageButton('zh').isVisible()) {
      return;
    }

    await this.userPanelToggleButton().click();
    await expect(this.quickLanguageButton('zh')).toBeVisible({ timeout: 5000 });
  }

  async openSettings(): Promise<void> {
    await this.openUserPanel();
    await this.openSettingsActionButton().click();
    await expect(this.modalOverlay).toBeVisible({ timeout: 5000 });
  }

  async closeSettings(): Promise<void> {
    await this.page.keyboard.press('Escape');
    await expect(this.modalOverlay).not.toBeVisible();
  }

  async openGeneralTab(): Promise<void> {
    await this.openSettings();
    await this.generalTab.click();
    await expect(this.page.getByTestId('language-button-zh')).toBeVisible();
  }

  async setLanguage(language: SupportedLanguage): Promise<void> {
    const languageButton = this.page.getByTestId(`language-button-${language}`);
    await languageButton.click();
    await expect(languageButton).toHaveClass(/border-\[hsl\(var\(--accent-primary\)\)\]/);
  }

  async expectLanguageStored(language: SupportedLanguage): Promise<void> {
    await expect
      .poll(async () => this.page.evaluate(() => window.localStorage.getItem('zenstory-language')))
      .toBe(language);
  }

  async expectProfileTabLabel(label: string): Promise<void> {
    await expect(this.profileTab).toContainText(label);
  }

  async setTheme(theme: ThemeMode): Promise<void> {
    const themeButton = this.page.getByTestId(`theme-button-${theme}`);
    await themeButton.click();
    await expect(themeButton).toHaveClass(/border-\[hsl\(var\(--accent-primary\)\)\]/);
  }

  async expectThemeStored(theme: ThemeMode): Promise<void> {
    await expect
      .poll(async () => this.page.evaluate(() => window.localStorage.getItem('zenstory-theme')))
      .toBe(theme);
  }

  async expectHtmlThemeClass(theme: ThemeMode): Promise<void> {
    await expect
      .poll(async () => this.page.evaluate((expectedTheme) => document.documentElement.classList.contains(expectedTheme), theme))
      .toBe(true);
  }

  async setAccentColor(color: string): Promise<void> {
    await this.page.locator(`[data-testid="accent-color-button"][data-color="${color}"]`).click();
  }

  async expectAccentStored(color: string): Promise<void> {
    await expect
      .poll(async () => this.page.evaluate(() => window.localStorage.getItem('zenstory-accent-color')))
      .toBe(color);
  }

  async expectAccentButtonSelected(color: string): Promise<void> {
    const button = this.page.locator(`[data-testid="accent-color-button"][data-color="${color}"]`);
    await expect(button).toHaveAttribute('aria-checked', 'true');
    await expect(button.locator('span')).toHaveClass(/ring-2/);
  }

  async expectAccentPrimaryCssVar(color: string): Promise<void> {
    const expected = ACCENT_PRIMARY_BY_COLOR[color];
    if (!expected) {
      throw new Error(`Unsupported accent color in regression assertion: ${color}`);
    }

    await expect
      .poll(async () => this.page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent-primary').trim()))
      .toBe(expected);
  }
}

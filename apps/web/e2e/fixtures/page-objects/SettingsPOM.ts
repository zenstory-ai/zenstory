import { Page, Locator, expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class SettingsPOM extends BasePage {
  readonly settingsDialog: Locator;
  readonly closeButton: Locator;
  readonly profileTab: Locator;
  readonly generalTab: Locator;
  readonly logoutButton: Locator;

  constructor(page: Page) {
    super(page);
    this.settingsDialog = page.locator('[role="dialog"]');
    this.closeButton = this.settingsDialog.getByRole('button', {
      name: /Close modal|关闭对话框/,
    });
    this.profileTab = page.getByTestId('settings-tab-profile');
    this.generalTab = page.getByTestId('settings-tab-general');
    this.logoutButton = page.getByTestId('settings-logout-button');
  }

  private userPanelToggleButton(): Locator {
    return this.page.getByTestId('dashboard-user-panel-toggle').first();
  }

  private openSettingsActionButton(): Locator {
    return this.page.getByTestId('dashboard-open-settings-button').first();
  }

  private quickLanguageButton(language: 'zh' | 'en'): Locator {
    const testId = language === 'zh' ? 'dashboard-quick-language-zh' : 'dashboard-quick-language-en';
    return this.page.getByTestId(testId).first();
  }

  /**
   * Open user panel from dashboard sidebar.
   */
  async openUserPanel(): Promise<void> {
    if (await this.quickLanguageButton('zh').isVisible()) {
      return;
    }

    await this.userPanelToggleButton().click();
    await expect(this.quickLanguageButton('zh')).toBeVisible({ timeout: 5000 });
  }

  /**
   * Open settings dialog from dashboard lower-left user panel.
   */
  async openSettings(): Promise<void> {
    await this.openUserPanel();
    await this.openSettingsActionButton().click();
    await expect(this.settingsDialog).toBeVisible({ timeout: 5000 });
  }

  async closeSettings(): Promise<void> {
    await this.closeButton.click();
    await expect(this.settingsDialog).not.toBeVisible();
  }

  async closeSettingsByEscape(): Promise<void> {
    await this.page.keyboard.press('Escape');
    await expect(this.settingsDialog).not.toBeVisible();
  }

  async closeSettingsByBackdropClick(): Promise<void> {
    const overlay = this.page.locator('div[role="presentation"]').filter({ has: this.settingsDialog }).first();
    await overlay.click({ position: { x: 8, y: 8 } });
    await expect(this.settingsDialog).not.toBeVisible();
  }

  async waitForDialog(): Promise<void> {
    await expect(this.settingsDialog).toBeVisible({ timeout: 5000 });
  }

  async isDialogOpen(): Promise<boolean> {
    return await this.settingsDialog.isVisible();
  }

  async switchToTab(tab: 'profile' | 'general'): Promise<void> {
    const tabButton = tab === 'profile' ? this.profileTab : this.generalTab;
    await tabButton.click();
    // Verify active state via the tab semantics, not the selected-state colour classes
    await expect(tabButton).toHaveAttribute('aria-selected', 'true');
  }

  async getActiveTab(): Promise<'profile' | 'general'> {
    const profileActive = await this.profileTab.getAttribute('aria-selected');
    if (profileActive === 'true') {
      return 'profile';
    }
    return 'general';
  }

  async getUserDisplayName(): Promise<string> {
    const nameElement = this.settingsDialog.locator('.font-medium.text-\\[hsl\\(var\\(--text-primary\\)\\)\\]').first();
    return await nameElement.textContent() || '';
  }

  async getUserEmail(): Promise<string> {
    const emailElement = this.settingsDialog.locator('.text-sm.text-\\[hsl\\(var\\(--text-secondary\\)\\)\\]').first();
    return await emailElement.textContent() || '';
  }

  async clickLogout(): Promise<void> {
    await this.logoutButton.click();
  }

  async getCurrentLanguage(): Promise<'zh' | 'en'> {
    const zhButton = this.page.getByTestId('language-button-zh');
    const zhClass = await zhButton.getAttribute('class');
    if (zhClass?.includes('border-[hsl(var(--accent-primary))]')) {
      return 'zh';
    }
    return 'en';
  }

  async setLanguage(lang: 'zh' | 'en'): Promise<void> {
    await this.page.getByTestId(`language-button-${lang}`).click();
    // Verify language change took effect
    await expect(this.page.getByTestId(`language-button-${lang}`)).toHaveAttribute(
      'class',
      /border-\[hsl\(var\(--accent-primary\)\)\]/
    );
  }

  async getCurrentTheme(): Promise<'dark' | 'light'> {
    const darkButton = this.page.getByTestId('theme-button-dark');
    const darkClass = await darkButton.getAttribute('class');
    if (darkClass?.includes('border-[hsl(var(--accent-primary))]')) {
      return 'dark';
    }
    return 'light';
  }

  async setTheme(theme: 'dark' | 'light'): Promise<void> {
    await this.page.getByTestId(`theme-button-${theme}`).click();
    // Verify theme change
    await expect(this.page.getByTestId(`theme-button-${theme}`)).toHaveAttribute(
      'class',
      /border-\[hsl\(var\(--accent-primary\)\)\]/
    );
  }

  async getCurrentAccentColor(): Promise<string> {
    const selectedColor = this.page.locator('[data-testid="accent-color-button"].ring-2');
    return await selectedColor.getAttribute('data-color') || '#4a9eff';
  }

  async setAccentColor(color: string): Promise<void> {
    await this.page.locator(`[data-testid="accent-color-button"][data-color="${color}"]`).click();
  }

  async getAvailableAccentColors(): Promise<string[]> {
    const buttons = this.page.getByTestId('accent-color-button');
    const count = await buttons.count();
    const colors: string[] = [];
    for (let i = 0; i < count; i++) {
      const color = await buttons.nth(i).getAttribute('data-color');
      if (color) colors.push(color);
    }
    return colors;
  }

  async isLoggingOut(): Promise<boolean> {
    const span = this.logoutButton.locator('span');
    const text = await span.textContent();
    return text?.includes('...') || text?.toLowerCase().includes('loading') || false;
  }
}

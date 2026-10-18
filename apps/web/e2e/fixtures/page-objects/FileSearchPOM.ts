import { Page, Locator, expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class FileSearchPOM extends BasePage {
  readonly searchInput: Locator;
  readonly clearButton: Locator;
  readonly resultsDropdown: Locator;

  constructor(page: Page) {
    super(page);
    this.searchInput = page.getByTestId('file-search-input');
    this.clearButton = page.getByTestId('file-search-clear-button');
    this.resultsDropdown = page.getByTestId('search-results-dropdown');
  }

  /**
   * PREREQUISITE: Must be on a project page (within Layout component)
   * Use loginAndNavigateToProject helper before calling this
   */
  async openSearch(): Promise<void> {
    // Cmd+K on Mac, Ctrl+K on Windows/Linux
    const modifier = process.platform === 'darwin' ? 'Meta' : 'Control';
    await this.page.keyboard.press(`${modifier}+k`);
    await expect(this.searchInput).toBeVisible({ timeout: 2000 });
  }

  async search(query: string): Promise<void> {
    await this.searchInput.fill(query);
    // Wait for debounced search (300ms debounce in useFileSearch hook)
    // Use explicit wait for results instead of timeout
    if (query.length > 0) {
      await expect(this.resultsDropdown).toBeVisible({ timeout: 3000 });
    }
  }

  async clearSearch(): Promise<void> {
    await this.clearButton.click();
  }

  async waitForResults(minCount: number = 1): Promise<void> {
    await expect(this.resultsDropdown.getByTestId(/search-result-item-/)).toHaveCount(minCount, { timeout: 5000 });
  }

  async getResultTitles(): Promise<string[]> {
    const items = this.resultsDropdown.getByTestId(/search-result-item-/);
    const count = await items.count();
    const titles: string[] = [];
    for (let i = 0; i < count; i++) {
      titles.push(await items.nth(i).textContent() || '');
    }
    return titles;
  }

  async selectResult(index: number): Promise<void> {
    const items = this.resultsDropdown.getByTestId(/search-result-item-/);
    await items.nth(index).click();
  }

  async selectResultByTitle(title: string): Promise<void> {
    await this.resultsDropdown.getByText(title).first().click();
  }

  async isDropdownVisible(): Promise<boolean> {
    return await this.resultsDropdown.isVisible();
  }

  async isLoading(): Promise<boolean> {
    // Check for loading indicator if component has one
    const loadingIndicator = this.resultsDropdown.getByTestId('search-results-loading');
    return await loadingIndicator.isVisible();
  }

  async isEmptyState(): Promise<boolean> {
    // Check for empty state message
    const emptyMessage = this.resultsDropdown.getByTestId('search-results-empty');
    return await emptyMessage.isVisible();
  }

  async closeSearch(): Promise<void> {
    await this.page.keyboard.press('Escape');
    await expect(this.resultsDropdown).not.toBeVisible();
  }

  async navigateResults(direction: 'up' | 'down'): Promise<void> {
    const key = direction === 'down' ? 'ArrowDown' : 'ArrowUp';
    await this.page.keyboard.press(key);
  }

  async getSelectedResultIndex(): Promise<number> {
    // Check which result has aria-selected="true"
    const items = this.resultsDropdown.getByTestId(/search-result-item-/);
    const count = await items.count();
    for (let i = 0; i < count; i++) {
      const isSelected = await items.nth(i).getAttribute('aria-selected');
      if (isSelected === 'true') return i;
    }
    return -1;
  }

  async typeWithIME(text: string): Promise<void> {
    await this.searchInput.focus();
    await this.searchInput.dispatchEvent('compositionstart', { data: '' });
    await this.page.keyboard.insertText(text);
    // Confirming an IME candidate must not select a search result prematurely.
    await this.searchInput.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true, keyCode: 229 });
    await this.searchInput.dispatchEvent('compositionend', { data: text });
  }
}

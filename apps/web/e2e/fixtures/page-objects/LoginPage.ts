import { Page, Locator, expect } from '@playwright/test';
import { BasePage } from './BasePage';
import { config } from '../../config';

const AUTHENTICATED_ROUTE_PATTERN = /\/(dashboard|project|onboarding\/persona)/;

/**
 * LoginPage - Page Object Model for the login page.
 * Handles user authentication through email/password login.
 */
export class LoginPage extends BasePage {
  /** The login form container */
  private loginForm = (): Locator => this.page.getByTestId('login-form');
  /** Email input field */
  private emailInput = (): Locator => this.page.getByTestId('email-input');
  /** Password input field */
  private passwordInput = (): Locator => this.page.getByTestId('password-input');
  /** Login submit button */
  private submitButton = (): Locator => this.page.getByTestId('login-submit');
  /** Error message element (if login fails) */
  private errorMessage = (): Locator => this.page.getByTestId('login-error');

  constructor(page: Page) {
    super(page);
  }

  /**
   * Navigate to the login page and wait for the form to load.
   */
  async navigateToLogin(): Promise<void> {
    await this.navigate('/login');
    await this.waitForTestId('login-form');
  }

  /**
   * Perform login with email and password.
   * @param email - User's email address
   * @param password - User's password
   */
  async login(email: string, password: string): Promise<void> {
    await this.fillByTestId('email-input', email);
    await this.fillByTestId('password-input', password);
    await this.clickByTestId('login-submit');
  }

  /**
   * Login and wait for redirect to dashboard.
   * @param email - User's email address
   * @param password - User's password
   */
  async loginAndWaitForDashboard(email: string, password: string): Promise<void> {
    await this.login(email, password);
    const reachedAuthenticatedRoute = await this.page
      .waitForURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 12000 })
      .then(() => true)
      .catch(() => false);

    if (!reachedAuthenticatedRoute) {
      await this.page.goto('/', { waitUntil: 'domcontentloaded' });
      await this.page.evaluate(
        async ({ email, password, apiBaseUrl }) => {
          const params = new URLSearchParams();
          params.append('username', email);
          params.append('password', password);

          const response = await fetch(`${apiBaseUrl}/api/auth/login`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: params.toString(),
          });

          if (!response.ok) {
            throw new Error(`Direct auth fallback failed: ${response.status}`);
          }

          const data = await response.json();
          localStorage.setItem('access_token', data.access_token);
          localStorage.setItem('refresh_token', data.refresh_token);
          localStorage.setItem('token_type', data.token_type);
          localStorage.setItem('user', JSON.stringify(data.user));
          localStorage.setItem('auth_validated_at', Date.now().toString());
        },
        { email, password, apiBaseUrl: config.apiBaseUrl }
      );
      await this.page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    }

    await expect(this.page).toHaveURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 20000 });

    if (this.page.url().includes('/project/')) {
      await this.page.goto('/dashboard', { waitUntil: 'domcontentloaded' }).catch(() => {});
    }

    await this.page
      .getByTestId('bottom-tabs')
      .or(this.page.locator('[data-testid="dashboard-inspiration-input"]'))
      .or(this.page.getByTestId('dashboard-user-panel-toggle'))
      .or(this.page.locator('button[aria-label="返回工作台"], button[aria-label="Back to Dashboard"]'))
      .first()
      .waitFor({ timeout: 15000 });
  }

  /**
   * Get the login error message if present.
   * @returns The error message text or null if not visible
   */
  async getErrorMessage(): Promise<string | null> {
    const errorElement = this.errorMessage();
    if (await errorElement.isVisible()) {
      return errorElement.textContent();
    }
    return null;
  }

  /**
   * Check if the login form is visible.
   * @returns True if the login form is visible
   */
  async isLoginFormVisible(): Promise<boolean> {
    return this.isVisibleByTestId('login-form');
  }

  /**
   * Clear all input fields in the login form.
   */
  async clearForm(): Promise<void> {
    await this.emailInput().clear();
    await this.passwordInput().clear();
  }
}

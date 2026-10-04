import { expect, test, type Page } from "@playwright/test";
import { TEST_USERS } from "./config";

const AUTHENTICATED_ROUTE_PATTERN = /\/(project|dashboard|onboarding\/persona)/;
const TEST_EMAIL = process.env.E2E_TEST_EMAIL || TEST_USERS.standard.email;
const TEST_PASSWORD = process.env.E2E_TEST_PASSWORD || TEST_USERS.standard.password;
const PERSONA_KEY_PREFIX = "zenstory_onboarding_persona_v1";

async function gotoWithRetry(
  page: Page,
  url: string,
  options: { attempts?: number; timeout?: number } = {}
) {
  const attempts = options.attempts ?? 2;
  const timeout = options.timeout ?? 25000;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout });
      await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
      return;
    } catch (error) {
      if (attempt === attempts) throw error;
      await page.waitForTimeout(500 * attempt);
    }
  }
}

async function loginAndSettle(page: Page) {
  await gotoWithRetry(page, "/login");

  await page.fill("input#identifier", TEST_EMAIL);
  await page.fill("input#password", TEST_PASSWORD);
  await page.click("button[type='submit']");
  await page.waitForURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 30000 });

  if (page.url().includes("/onboarding/persona")) {
    await page.evaluate((keyPrefix) => {
      const rawUser = localStorage.getItem("user");
      if (!rawUser) return;
      const user = JSON.parse(rawUser) as { id?: string };
      if (!user.id) return;
      localStorage.setItem(
        `${keyPrefix}:${user.id}`,
        JSON.stringify({
          version: 1,
          completed_at: new Date().toISOString(),
          selected_personas: ["explorer"],
          selected_goals: ["finishBook"],
          experience_level: "beginner",
          skipped: false,
        })
      );
    }, PERSONA_KEY_PREFIX);

    await gotoWithRetry(page, "/dashboard");
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10000 });
  }
}

async function clearCurrentUserPersonaStorage(page: Page) {
  await page.evaluate((keyPrefix) => {
    const rawUser = localStorage.getItem("user");
    if (!rawUser) return;
    const user = JSON.parse(rawUser) as { id?: string };
    if (!user.id) return;
    localStorage.removeItem(`${keyPrefix}:${user.id}`);
  }, PERSONA_KEY_PREFIX);
}

test.describe("Onboarding persona flow", () => {
  let lastSubmittedPayload: {
    selected_personas: string[];
    selected_goals: string[];
    experience_level: string;
    skipped: boolean;
  } | null = null;

  test.beforeEach(async ({ page }) => {
    lastSubmittedPayload = null;
    await page.route("**/api/v1/persona/onboarding", async (route) => {
      const method = route.request().method();

      if (method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            required: true,
            rollout_at: "2024-01-01T00:00:00Z",
            new_user_window_days: 30,
            profile: null,
            recommendations: [],
          }),
        });
        return;
      }

      if (method === "PUT") {
        lastSubmittedPayload = JSON.parse(route.request().postData() ?? "{}");
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            required: false,
            rollout_at: "2024-01-01T00:00:00Z",
            new_user_window_days: 30,
            profile: {
              version: 1,
              completed_at: new Date().toISOString(),
              ...lastSubmittedPayload,
            },
            recommendations: [],
          }),
        });
        return;
      }

      await route.continue();
    });

    await loginAndSettle(page);
    await clearCurrentUserPersonaStorage(page);
    await gotoWithRetry(page, "/onboarding/persona");
    await expect(page.getByText(/先认识你|let us know you first/i)).toBeVisible();
  });

  test("submits persona selection and persists onboarding payload", async ({ page }) => {
    await page.getByRole("button", { name: /新手探索者|beginner explorer/i }).click();
    await page.getByRole("button", { name: /尽快实现内容变现|monetize content sooner/i }).click();
    await page.getByRole("radio", { name: /2 年以上|2\+ years/i }).click();
    await page.getByRole("button", { name: /保存并进入工作台|save and enter workspace/i }).click();

    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 15000 });
    expect(lastSubmittedPayload).toMatchObject({
      selected_personas: ["explorer"],
      selected_goals: ["monetize"],
      experience_level: "advanced",
      skipped: false,
    });

    const savedPayload = await page.evaluate((keyPrefix) => {
      const rawUser = localStorage.getItem("user");
      if (!rawUser) return null;
      const user = JSON.parse(rawUser) as { id?: string };
      if (!user.id) return null;
      const raw = localStorage.getItem(`${keyPrefix}:${user.id}`);
      return raw ? JSON.parse(raw) : null;
    }, PERSONA_KEY_PREFIX);

    expect(savedPayload).not.toBeNull();
    expect(savedPayload?.selected_personas).toEqual(["explorer"]);
    expect(savedPayload?.selected_goals).toEqual(["monetize"]);
    expect(savedPayload?.experience_level).toBe("advanced");
    expect(savedPayload?.skipped).toBe(false);
  });

  test("shows limit warning when selecting more than three personas", async ({ page }) => {
    await page.getByRole("button", { name: /新手探索者|beginner explorer/i }).click();
    await page.getByRole("button", { name: /连载成长型|serial growth writer/i }).click();
    await page.getByRole("button", { name: /职业创作者|professional creator/i }).click();
    await page.getByRole("button", { name: /同人\/兴趣创作者|fanfic \/ hobby creator/i }).click();

    await expect(
      page.getByText(/最多可选 3 项|up to 3 personas/i)
    ).toBeVisible();
    await expect(page.getByText(/3 \/ 3 已选|3 \/ 3 selected/i)).toBeVisible();
  });

  test("skip action stores skipped payload and redirects to dashboard", async ({ page }) => {
    await page.getByRole("button", { name: /暂时跳过|skip for now/i }).click();

    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 15000 });

    const savedPayload = await page.evaluate((keyPrefix) => {
      const rawUser = localStorage.getItem("user");
      if (!rawUser) return null;
      const user = JSON.parse(rawUser) as { id?: string };
      if (!user.id) return null;
      const raw = localStorage.getItem(`${keyPrefix}:${user.id}`);
      return raw ? JSON.parse(raw) : null;
    }, PERSONA_KEY_PREFIX);

    expect(savedPayload).not.toBeNull();
    expect(savedPayload?.selected_personas).toEqual([]);
    expect(savedPayload?.selected_goals).toEqual([]);
    expect(savedPayload?.skipped).toBe(true);
  });
});

test.describe("Dashboard nav regression", () => {
  test.beforeEach(async ({ page }) => {
    await loginAndSettle(page);
    await gotoWithRetry(page, "/dashboard");
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10000 });
  });

  test("does not show deprecated Lab entry", async ({ page }) => {
    await expect(
      page.getByRole("button", { name: /实验室|lab/i })
    ).toHaveCount(0);
  });
});

test.describe("Onboarding gate regression", () => {
  test("existing seeded user should not be forced into onboarding route", async ({ page }) => {
    await gotoWithRetry(page, "/login");
    await page.fill("input#identifier", TEST_EMAIL);
    await page.fill("input#password", TEST_PASSWORD);
    await page.click("button[type='submit']");

    await page.waitForURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 30000 });
    expect(page.url()).not.toContain("/onboarding/persona");
  });

  test("server-required legacy session is redirected from a protected route", async ({ page }) => {
    // Keep the real legacy account: only the server response should require onboarding.
    let onboardingReads = 0;
    await page.route("**/api/v1/persona/onboarding", async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      onboardingReads += 1;
      await route.fulfill({
        status: 200,
        json: {
          required: true, rollout_at: "2026-03-05T16:00:00Z", new_user_window_days: 7,
          profile: null, recommendations: [],
        },
      });
    });

    await gotoWithRetry(page, "/login");
    await page.fill("input#identifier", TEST_EMAIL);
    await page.fill("input#password", TEST_PASSWORD);
    await page.click("button[type='submit']");
    await page.waitForURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 30000 });

    await gotoWithRetry(page, "/dashboard/projects");
    await expect(page).toHaveURL(/\/onboarding\/persona/, { timeout: 10000 });
    expect(onboardingReads).toBeGreaterThan(0);
  });
});

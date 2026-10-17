import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockNavigate, mockLanguage, mockAuthConfig, mockAuthState } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockLanguage: { value: "en-US" },
  mockAuthState: { user: null as { id: string } | null },
  mockAuthConfig: {
    registrationEnabled: true,
  },
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: unknown) =>
      options && typeof options === "object" && "year" in options
        ? `${key}|year=${String((options as { year: unknown }).year)}`
        : key,
    i18n: {
      language: mockLanguage.value,
      resolvedLanguage: mockLanguage.value,
    },
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: mockAuthState.user,
  }),
}));

vi.mock("../../hooks/usePreloadRoute", () => ({
  usePreloadRoute: () => vi.fn(),
}));

vi.mock("../../config/auth", () => ({
  authConfig: mockAuthConfig,
}));

vi.mock("../../components/PublicHeader", () => ({
  PublicHeader: () => <div data-testid="public-header" />,
}));

import HomePage from "../HomePage";
import {
  PREFERRED_PROJECT_TYPE_STORAGE_KEY,
  getPreferredProjectType,
  setPreferredProjectType,
} from "../../lib/preferredProjectType";

const setupMatchMedia = (reducedMotion: boolean) => {
  return vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: query === "(prefers-reduced-motion: reduce)" ? reducedMotion : false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
};

describe("HomePage reduced-motion carousel behavior", () => {
  let mockedNow = 0;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mockLanguage.value = "en-US";
    mockAuthConfig.registrationEnabled = true;
    mockAuthState.user = null;
    localStorage.clear();
    mockedNow = 0;
    vi.spyOn(Date, "now").mockImplementation(() => mockedNow);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("keeps the app footer focused on legal links and copyright", () => {
    setupMatchMedia(true);
    const { container } = render(<MemoryRouter><HomePage /></MemoryRouter>);
    const footer = container.querySelector('footer')!;
    expect(Array.from(footer.querySelectorAll('a'), link => link.getAttribute('href')))
      .toEqual(['/privacy-policy', '/terms-of-service']);
    expect(footer).toHaveTextContent('home:footer.copyright');
  });

  it("does not auto-rotate scenes when reduced-motion is enabled", () => {
    setupMatchMedia(true);

    const { container } = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    const createButton = screen.getByRole("button", { name: "home:demo.scenes.create" });
    const suggestButton = screen.getByRole("button", { name: "home:demo.scenes.suggest" });

    expect(createButton.className).toContain("text-white");
    expect(suggestButton.className).not.toContain("text-white");
    expect(container.querySelector('div[style*="width"]')).toBeNull();

    act(() => {
      mockedNow = 20000;
      vi.advanceTimersByTime(20000);
    });

    expect(createButton.className).toContain("text-white");
    expect(suggestButton.className).not.toContain("text-white");
  });

  it("auto-rotates to the next scene when reduced-motion is disabled", () => {
    setupMatchMedia(false);

    const { container } = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    const createButton = screen.getByRole("button", { name: "home:demo.scenes.create" });
    const suggestButton = screen.getByRole("button", { name: "home:demo.scenes.suggest" });

    expect(createButton.className).toContain("text-white");
    expect(suggestButton.className).not.toContain("text-white");
    expect(container.querySelector('div[style*="width"]')).not.toBeNull();

    act(() => {
      mockedNow = 5600;
      vi.advanceTimersByTime(5600);
    });

    expect(createButton.className).not.toContain("text-white");
    expect(suggestButton.className).toContain("text-white");
  });

  it("keeps playback actions, progress, and scene position in one control row", () => {
    setupMatchMedia(false);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    const controls = screen.getByTestId("home-demo-playback-controls");
    expect(controls).toContainElement(screen.getByRole("button", { name: "home:demo.pause" }));
    expect(controls).toContainElement(screen.getByRole("progressbar", { name: "home:demo.progressLabel" }));
    expect(controls).toHaveTextContent("1 / 3");
  });

  it("gives every project type card the same anchored content slots", () => {
    setupMatchMedia(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    for (const type of ["novel", "short", "screenplay"]) {
      const card = screen.getByTestId(`home-project-type-card-${type}`);
      expect(card.querySelector('[data-card-slot="header"]')).not.toBeNull();
      expect(card.querySelector('[data-card-slot="description"]')).not.toBeNull();
      expect(card.querySelector('[data-card-slot="features"]')).not.toBeNull();
    }
  });

  it("does not render invented social-proof metrics", () => {
    setupMatchMedia(true);

    for (const language of ["en-US", "zh-CN"]) {
      mockLanguage.value = language;
      const { unmount } = render(
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      );

      expect(screen.queryByText(/home:stats\./)).not.toBeInTheDocument();
      expect(screen.queryByText(/2,000\+|2000\+/)).not.toBeInTheDocument();
      expect(screen.queryByText(/^4\.9$/)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("stamps the footer copyright with the current year", () => {
    setupMatchMedia(true);
    vi.setSystemTime(new Date("2031-05-01T00:00:00Z"));

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    expect(screen.getByText("home:footer.copyright|year=2031")).toBeInTheDocument();
  });

  it("adds source attribution and preserves plan for core homepage CTA entries", () => {
    setupMatchMedia(true);

    render(
      <MemoryRouter initialEntries={["/?plan=pro"]}>
        <HomePage />
      </MemoryRouter>
    );

    expect(screen.getByRole("link", { name: "home:pricingTeaser.viewPricing" })).toHaveAttribute(
      "href",
      "/pricing?plan=pro&source=home_pricing_teaser"
    );

    fireEvent.click(screen.getByRole("button", { name: "home:hero.cta" }));
    expect(mockNavigate).toHaveBeenLastCalledWith("/register?plan=pro&source=home_hero");

    fireEvent.click(screen.getByRole("button", { name: "home:pricingTeaser.primaryCta" }));
    expect(mockNavigate).toHaveBeenLastCalledWith("/register?plan=pro&source=home_pricing_teaser");

    fireEvent.click(screen.getByRole("button", { name: "home:cta.button" }));
    expect(mockNavigate).toHaveBeenLastCalledWith("/register?plan=pro&source=home_cta");
  });

  it("adds source attribution for project type card entry", () => {
    setupMatchMedia(true);

    render(
      <MemoryRouter initialEntries={["/?plan=pro"]}>
        <HomePage />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole("button", { name: /home:projectTypes\.novel\.name/ }));
    expect(mockNavigate).toHaveBeenCalledWith("/register?plan=pro&source=home_project_type_card");
  });

  it("remembers the clicked project type before continuing to sign-up", () => {
    setupMatchMedia(true);
    localStorage.removeItem(PREFERRED_PROJECT_TYPE_STORAGE_KEY);
    const order: string[] = [];
    mockNavigate.mockImplementation(() => {
      order.push(`navigate:${getPreferredProjectType() ?? "none"}`);
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole("button", { name: /home:projectTypes\.screenplay\.name/ }));

    expect(getPreferredProjectType()).toBe("screenplay");
    // The preference is stored before navigation, so the dashboard sees it.
    expect(order).toEqual(["navigate:screenplay"]);
    expect(mockNavigate).toHaveBeenCalledWith("/register?source=home_project_type_card");

    fireEvent.click(screen.getByRole("button", { name: /home:projectTypes\.short\.name/ }));
    expect(getPreferredProjectType()).toBe("short");
    localStorage.removeItem(PREFERRED_PROJECT_TYPE_STORAGE_KEY);
  });

  it("renders project type cards as keyboard-focusable buttons", () => {
    setupMatchMedia(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    const novelCard = screen.getByRole("button", { name: /home:projectTypes\.novel\.name/ });
    expect(novelCard.className).toContain("focus-visible:ring-2");
  });

  it("stores an authenticated public-entry choice only for that account", () => {
    setupMatchMedia(true);
    mockAuthState.user = { id: "A" };
    setPreferredProjectType("short", "A");
    render(<MemoryRouter><HomePage /></MemoryRouter>);

    fireEvent.click(screen.getByTestId("home-project-type-card-screenplay"));

    expect(getPreferredProjectType("A")).toBe("screenplay");
    expect(getPreferredProjectType("B")).toBeNull();
    expect(getPreferredProjectType()).toBeNull();
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard");
  });

  it("shows an opening state on the clicked type card while the sign-up page loads", () => {
    setupMatchMedia(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    const dramaCard = screen.getByTestId("home-project-type-card-screenplay");
    expect(dramaCard).toHaveAttribute("aria-busy", "false");
    expect(screen.queryByTestId("home-project-type-card-opening")).not.toBeInTheDocument();

    fireEvent.click(dramaCard);

    expect(dramaCard).toHaveAttribute("aria-busy", "true");
    expect(screen.getByTestId("home-project-type-card-opening")).toHaveTextContent("home:projectTypes.opening");
    expect(screen.getByTestId("home-project-type-card-novel")).toHaveAttribute("aria-busy", "false");
    localStorage.removeItem(PREFERRED_PROJECT_TYPE_STORAGE_KEY);
  });

  it("falls back to login entry while keeping source attribution when registration is disabled", () => {
    setupMatchMedia(true);
    mockAuthConfig.registrationEnabled = false;

    render(
      <MemoryRouter initialEntries={["/?plan=pro"]}>
        <HomePage />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole("button", { name: "home:hero.cta" }));
    expect(mockNavigate).toHaveBeenCalledWith("/login?plan=pro&source=home_hero");

    fireEvent.click(screen.getByRole("button", { name: /home:projectTypes\.novel\.name/ }));
    expect(mockNavigate).toHaveBeenCalledWith("/login?plan=pro&source=home_project_type_card");
  });
});

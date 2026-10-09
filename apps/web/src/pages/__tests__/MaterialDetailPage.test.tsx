import { act, render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/apiClient";

const mockNavigate = vi.fn();
const mockGet = vi.fn();
const mockGetTree = vi.fn();
const mockGetChapter = vi.fn();
const mockGetCharacters = vi.fn();
const mockGetStories = vi.fn();
const mockGetPlots = vi.fn();
const mockGetStoryLines = vi.fn();
const mockGetRelationships = vi.fn();
const mockGetGoldenFingers = vi.fn();
const mockGetWorldView = vi.fn();
const mockRetry = vi.fn();
const materialsConfigState = vi.hoisted(() => ({
  relationshipsEnabled: false,
}));
const mediaState = vi.hoisted(() => ({
  isMobile: false,
}));
const routeState = vi.hoisted(() => ({
  novelId: "novel-1",
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useParams: () => ({ novelId: routeState.novelId }),
  };
});

vi.mock("react-i18next", () => ({
  initReactI18next: {
    type: "3rdParty",
    init: vi.fn(),
  },
  useTranslation: () => ({
    t: (
      key: string,
      defaultValueOrOptions?: string | Record<string, unknown>,
    ) => {
      if (typeof defaultValueOrOptions === "string") return defaultValueOrOptions;
      if (
        defaultValueOrOptions &&
        typeof defaultValueOrOptions === "object" &&
        "defaultValue" in defaultValueOrOptions &&
        typeof defaultValueOrOptions.defaultValue === "string"
      ) {
        return defaultValueOrOptions.defaultValue;
      }
      if (
        defaultValueOrOptions &&
        typeof defaultValueOrOptions === "object" &&
        "number" in defaultValueOrOptions
      ) {
        return `${key} ${String(defaultValueOrOptions.number)}`;
      }
      return key;
    },
  }),
}));

vi.mock("../../hooks/useMediaQuery", () => ({
  useIsMobile: () => mediaState.isMobile,
}));

vi.mock("../../config/materials", () => ({
  materialsConfig: {
    get relationshipsEnabled() {
      return materialsConfigState.relationshipsEnabled;
    },
  },
}));

vi.mock("../../lib/materialsApi", () => ({
  materialsApi: {
    get: (...args: unknown[]) => mockGet(...args),
    getTree: (...args: unknown[]) => mockGetTree(...args),
    getChapter: (...args: unknown[]) => mockGetChapter(...args),
    getCharacters: (...args: unknown[]) => mockGetCharacters(...args),
    getStories: (...args: unknown[]) => mockGetStories(...args),
    getPlots: (...args: unknown[]) => mockGetPlots(...args),
    getStoryLines: (...args: unknown[]) => mockGetStoryLines(...args),
    getRelationships: (...args: unknown[]) => mockGetRelationships(...args),
    getGoldenFingers: (...args: unknown[]) => mockGetGoldenFingers(...args),
    getWorldView: (...args: unknown[]) => mockGetWorldView(...args),
    retry: (...args: unknown[]) => mockRetry(...args),
  },
}));

const mockGetStatus = vi.fn();
vi.mock("../../lib/subscriptionApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/subscriptionApi")>()),
  subscriptionApi: { getStatus: () => mockGetStatus() },
}));

vi.mock("../../lib/errorHandler", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/errorHandler")>()),
  translateError: (code: string) => `translated:${code}`,
}));

vi.mock("../../components/subscription/MaterialsUpgradePrompt", () => ({
  MaterialsUpgradeNotice: ({ source }: { source: string }) => (
    <div data-testid="materials-upgrade-notice">{source}</div>
  ),
}));

import MaterialDetailPage from "../MaterialDetailPage";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

describe("MaterialDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    materialsConfigState.relationshipsEnabled = false;
    mediaState.isMobile = false;
    routeState.novelId = "novel-1";
    mockGetStatus.mockResolvedValue({ tier: "pro", features: { materials_library_access: true } });
    mockGetTree.mockResolvedValue({
      tree: [{ id: "chapter-node-1", type: "chapter", title: "Opening Chapter", metadata: { chapter_number: 1 } }],
    });
    mockGetChapter.mockResolvedValue({
      id: "chapter-1",
      title: "Opening Chapter",
      chapter_number: 1,
      word_count: 1200,
      summary: "Chapter summary",
      content: "Chapter body",
    });
    mockGetCharacters.mockResolvedValue([
      {
        id: "character-1",
        name: "Li Wei",
        aliases: ["Blade"],
        description: "A fearless lead",
        first_appearance_chapter_id: 101,
        first_appearance_chapter: 7,
      },
    ]);
    mockGetStories.mockResolvedValue([
      {
        id: "story-1",
        title: "Main arc",
        synopsis: "Story synopsis",
        story_type: "Adventure",
        core_objective: "Save the city",
        core_conflict: "Enemy invasion",
        themes: "[\"hope\",\"sacrifice\"]",
        chapter_range: "1-5",
      },
    ]);
    mockGetPlots.mockResolvedValue([
      {
        id: 1,
        description: "Hidden betrayal",
        plot_type: "Twist",
        characters: ["Li Wei", "Mentor"],
      },
    ]);
    mockGetStoryLines.mockResolvedValue([
      {
        id: 1,
        title: "Revenge path",
        description: "A revenge storyline",
        main_characters: ["Li Wei"],
        themes: ["justice"],
        stories_count: 2,
      },
    ]);
    mockGetRelationships.mockResolvedValue([
      {
        id: 1,
        character_a_name: "Li Wei",
        character_b_name: "Mentor",
      },
    ]);
    mockGetGoldenFingers.mockResolvedValue([
      {
        id: 1,
        name: "Phoenix Core",
      },
    ]);
    mockGetWorldView.mockResolvedValue({
      id: 1,
      power_system: "Qi",
      world_structure: "Three realms",
      key_forces: ["Court"],
    });
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Novel One",
      status: "completed",
      chapters_count: 0,
      characters_count: 0,
      story_lines_count: 0,
      golden_fingers_count: 0,
      has_world_view: false,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
  });

  it("hides relationships folder by default", async () => {
    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Novel One")).toBeInTheDocument();
    });

    expect(screen.queryByText("materials:detail.relationships")).not.toBeInTheDocument();
  });

  it("shows stored historical relationships while the global producer flag is off", async () => {
    mockGet.mockResolvedValueOnce({
      id: "novel-1", title: "Historical Novel", status: "completed", chapters_count: 1,
      relationships_count: 2,
      enabled_stages: { relationships: false },
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    });
    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Historical Novel")).toBeInTheDocument();
    expect(screen.getByText("materials:detail.relationships")).toBeInTheDocument();
  });

  it("distinguishes a genuine 404 from a retryable detail failure", async () => {
    mockGet.mockRejectedValueOnce(new Error("network down"));
    const { unmount } = render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("素材详情加载失败，请重试。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    unmount();

    mockGet.mockRejectedValueOnce(new ApiError(404, "not found"));
    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("materials:detail.notFound")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("shows the upgrade notice instead of a load error when the plan lacks the materials library", async () => {
    mockGet.mockRejectedValueOnce(new ApiError(402, "ERR_FEATURE_NOT_INCLUDED"));

    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("materials-upgrade-notice")).toHaveTextContent("material_detail");
    expect(screen.queryByText("素材详情加载失败，请重试。")).not.toBeInTheDocument();
  });

  it("shows the failure reason and a retry entry for a failed decomposition", async () => {
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Novel One",
      status: "failed",
      error_message: "ERR_MATERIAL_LLM_UNAVAILABLE",
      chapters_count: 0,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
    mockRetry.mockResolvedValue({ message: "ok", job_id: 2, status: "pending" });

    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("拆解失败");
    expect(banner).toHaveTextContent("translated:ERR_MATERIAL_LLM_UNAVAILABLE");

    fireEvent.click(await screen.findByRole("button", { name: "重试" }));
    await waitFor(() => expect(mockRetry).toHaveBeenCalledWith("novel-1"));
  });

  it("offers no retry to a free-trial author, whose retry could only be refused", async () => {
    mockGetStatus.mockResolvedValue({ tier: "free", features: { materials_library_access: false } });
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Novel One",
      status: "failed",
      error_message: "ERR_MATERIAL_DECOMPOSE_FAILED",
      chapters_count: 0,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    expect(await screen.findByRole("alert")).toHaveTextContent("拆解失败");
    await waitFor(() => expect(mockGetStatus).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("shows relationships folder when relationships UI is enabled", async () => {
    materialsConfigState.relationshipsEnabled = true;

    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Novel One")).toBeInTheDocument();
    });

    expect(screen.getByText("materials:detail.relationships")).toBeInTheDocument();
  });

  it("retries lazy folder loading after an initial failure", async () => {
    mockGetCharacters
      .mockRejectedValueOnce(new Error("temporary character load failure"))
      .mockResolvedValueOnce([
        {
          id: "character-retry",
          name: "Retry Hero",
          description: "Loaded after retry",
        },
      ]);

    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Novel One")).toBeInTheDocument();
    });

    const charactersFolder = screen.getByRole("button", {
      name: /materials:detail.characters/,
    });

    fireEvent.click(charactersFolder);
    await waitFor(() => {
      expect(mockGetCharacters).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText("加载失败。")).toBeInTheDocument();

    fireEvent.click(charactersFolder);
    fireEvent.click(charactersFolder);

    await waitFor(() => {
      expect(mockGetCharacters).toHaveBeenCalledTimes(2);
    });
    expect(await screen.findByText("Retry Hero")).toBeInTheDocument();
  });

  it("loads folder content and renders chapter, character, and story details", async () => {
    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Novel One")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.chapters/ }));
    await waitFor(() => {
      expect(screen.getByText("Opening Chapter")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Opening Chapter/ }));
    expect(await screen.findByText("Chapter summary")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.characters/ }));
    await waitFor(() => {
      expect(screen.getByText("Li Wei")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Li Wei/ }));
    expect(await screen.findByText("A fearless lead")).toBeInTheDocument();
    expect(screen.getByText(/materials:detail.firstAppearance/)).toHaveTextContent("7");

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.stories/ }));
    await waitFor(() => {
      expect(screen.getByText("Main arc")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Main arc/ }));
    expect(await screen.findByText("Story synopsis")).toBeInTheDocument();
    expect(screen.getByText("Save the city")).toBeInTheDocument();
  });

  it("loads chapter tree metadata on expand and chapter detail only on selection", async () => {
    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Novel One")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.chapters/ }));
    expect(await screen.findByText("Opening Chapter")).toBeInTheDocument();
    expect(mockGetTree).toHaveBeenCalledTimes(1);
    expect(mockGetChapter).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Opening Chapter/ }));
    expect(await screen.findByText("Chapter summary")).toBeInTheDocument();
    expect(mockGetChapter).toHaveBeenCalledTimes(1);
    expect(mockGetChapter).toHaveBeenCalledWith("novel-1", "chapter-node-1");
  });

  it("loads visible unexpanded folder metadata for search", async () => {
    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Novel One")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("materials:detail.searchPlaceholder"), {
      target: { value: "Li Wei" },
    });

    expect(await screen.findByText("Li Wei")).toBeInTheDocument();
    expect(mockGetCharacters).toHaveBeenCalledTimes(1);
  });

  it("keeps failed search folders retryable instead of presenting a complete empty result", async () => {
    mockGetCharacters
      .mockRejectedValueOnce(new Error("temporary search failure"))
      .mockResolvedValueOnce([{ id: "retry-search", name: "Retry Search Hero" }]);
    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Novel One")).toBeInTheDocument();

    const search = screen.getByPlaceholderText("materials:detail.searchPlaceholder");
    fireEvent.change(search, { target: { value: "Retry Search" } });
    await waitFor(() => expect(mockGetCharacters).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: /materials:detail.characters/ })).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "Retry Search Hero" } });
    expect(await screen.findByText("Retry Search Hero")).toBeInTheDocument();
    expect(mockGetCharacters).toHaveBeenCalledTimes(2);
  });

  it("resets lazy folder state when navigating to another novel", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><MaterialDetailPage /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Novel One")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /materials:detail.characters/ }));
    expect(await screen.findByText("Li Wei")).toBeInTheDocument();

    routeState.novelId = "novel-2";
    mockGet.mockResolvedValueOnce({
      id: "novel-2",
      title: "Novel Two",
      status: "completed",
      chapters_count: 0,
      created_at: "2026-01-02T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    });
    mockGetCharacters.mockResolvedValueOnce([{ id: "character-2", name: "New Hero" }]);
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><MaterialDetailPage /></MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Novel Two")).toBeInTheDocument();
    expect(screen.queryByText("Li Wei")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /materials:detail.characters/ }));
    expect(await screen.findByText("New Hero")).toBeInTheDocument();
    expect(mockGetCharacters).toHaveBeenLastCalledWith("novel-2");
  });

  it("hides explicitly disabled confirmed-empty folders without requesting them", async () => {
    materialsConfigState.relationshipsEnabled = true;
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Lean Novel",
      status: "completed",
      chapters_count: 1,
      plots_count: 0,
      stories_count: 0,
      story_lines_count: 0,
      relationships_count: 0,
      enabled_stages: {
        chapter_summaries: true,
        plots: false,
        characters: true,
        meta: true,
        synopsis: true,
        stories: false,
        storylines: false,
        relationships: false,
      },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Lean Novel")).toBeInTheDocument();

    expect(screen.queryByText("materials:detail.plots")).not.toBeInTheDocument();
    expect(screen.queryByText("materials:detail.stories")).not.toBeInTheDocument();
    expect(screen.queryByText("materials:detail.storylines")).not.toBeInTheDocument();
    expect(screen.queryByText("materials:detail.relationships")).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("materials:detail.searchPlaceholder"), {
      target: { value: "hidden" },
    });
    expect(mockGetPlots).not.toHaveBeenCalled();
    expect(mockGetStories).not.toHaveBeenCalled();
    expect(mockGetStoryLines).not.toHaveBeenCalled();
    expect(mockGetRelationships).not.toHaveBeenCalled();
  });

  it("keeps an explicitly disabled folder visible when its count is unknown", async () => {
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Unknown History Novel",
      status: "completed",
      chapters_count: 1,
      enabled_stages: {
        chapter_summaries: true,
        plots: false,
        characters: true,
        meta: true,
        synopsis: true,
        stories: true,
        relationships: false,
      },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Unknown History Novel")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /materials:detail.plots/ })).toBeEnabled();
  });

  it("loads plot, storyline, worldview, and goldenfinger folders and hides timeline", async () => {
    materialsConfigState.relationshipsEnabled = true;
    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Novel One")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.plots/ }));
    await waitFor(() => {
      expect(screen.getByText(/Hidden betrayal/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.storylines/ }));
    await waitFor(() => {
      expect(screen.getByText("Revenge path")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.worldview/ }));
    await waitFor(() => {
      expect(screen.getByText("materials:detail.worldviewItem")).toBeInTheDocument();
    });

    expect(screen.queryByText("materials:detail.timeline")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.goldenfingers/ }));
    await waitFor(() => {
      expect(screen.getByText("Phoenix Core")).toBeInTheDocument();
    });

    expect(screen.getByText("materials:detail.relationships")).toBeInTheDocument();
    expect(screen.queryByText("materials:detail.notEnabled")).not.toBeInTheDocument();
  });

  it("shows pending materials as active instead of an empty detail", async () => {
    mockGet.mockResolvedValueOnce({
      id: "novel-1",
      title: "Queued Novel",
      original_filename: "queued.txt",
      file_size: 123,
      status: "pending",
      chapters_count: 0,
      characters_count: 0,
      story_lines_count: 0,
      golden_fingers_count: 0,
      has_world_view: false,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    expect(await screen.findByText("Queued Novel")).toBeInTheDocument();
    expect(screen.getByText("materials:status.pending")).toBeInTheDocument();
  });

  it("polls active material details until processing finishes", async () => {
    vi.useFakeTimers();
    try {
      mockGet
        .mockResolvedValueOnce({
          id: "novel-1",
          title: "Queued Novel",
          original_filename: "queued.txt",
          file_size: 123,
          status: "pending",
          chapters_count: 0,
          characters_count: 0,
          story_lines_count: 0,
          golden_fingers_count: 0,
          has_world_view: false,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        })
        .mockResolvedValueOnce({
          id: "novel-1",
          title: "Ready Novel",
          original_filename: "queued.txt",
          file_size: 123,
          status: "completed",
          chapters_count: 1,
          characters_count: 0,
          story_lines_count: 0,
          golden_fingers_count: 0,
          has_world_view: false,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:03Z",
        });

      render(<MaterialDetailPage />, { wrapper: createWrapper() });

      await act(async () => {
        await vi.runOnlyPendingTimersAsync();
      });

      expect(screen.getByText("Ready Novel")).toBeInTheDocument();
      expect(mockGet).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refetches an expanded empty folder when processing becomes terminal", async () => {
    mockGet.mockResolvedValueOnce({
      id: "novel-1",
      title: "Growing Novel",
      status: "processing",
      chapters_count: 1,
      characters_count: 0,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
    mockGetCharacters
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: "late-character", name: "Late Hero" }]);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><MaterialDetailPage /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Growing Novel")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.characters/ }));
    await waitFor(() => expect(mockGetCharacters).toHaveBeenCalledTimes(1));

    act(() => {
      queryClient.setQueryData(["material", "novel-1"], {
        id: "novel-1",
        title: "Growing Novel",
        status: "completed",
        chapters_count: 1,
        characters_count: 1,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:03Z",
      });
    });

    expect(await screen.findByText("Late Hero")).toBeInTheDocument();
    expect(mockGetCharacters).toHaveBeenCalledTimes(2);
  });

  it("renders loading and not-found states", async () => {
    mockGet.mockImplementationOnce(() => new Promise(() => {}));
    const { unmount } = render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(document.querySelector(".animate-spin")).toBeInTheDocument();
    unmount();

    mockGet.mockResolvedValueOnce(null);
    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("materials:detail.notFound")).toBeInTheDocument();
  });

  it("supports mobile search and detail switching", async () => {
    mediaState.isMobile = true;
    render(<MaterialDetailPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Novel One")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText("materials:detail.searchPlaceholder"), {
      target: { value: "Li Wei" },
    });
    expect(await screen.findByRole("button", { name: /Li Wei/ })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /materials:detail.chapters/ })).not.toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText("materials:detail.searchPlaceholder"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: /materials:detail.characters/ }));
    await waitFor(() => {
      expect(screen.getByText("Li Wei")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Li Wei/ }));
    expect(await screen.findByText("A fearless lead")).toBeInTheDocument();
  });

  it("renders relationship, worldview, and goldenfinger details", async () => {
    materialsConfigState.relationshipsEnabled = true;
    mockGetRelationships.mockResolvedValueOnce([
      {
        id: 1,
        character_a_name: "Li Wei",
        character_b_name: "Mentor",
        relationship_type: "Ally",
        sentiment: "Trust",
        description: "Their bond evolves",
      },
    ]);
    mockGetGoldenFingers.mockResolvedValueOnce([
      {
        id: 1,
        name: "Phoenix Core",
        type: "Artifact",
        description: "Stores ancient power",
        evolution_history: [
          { stage: "Dormant", description: "Sleeping", chapter: "12", timestamp: "Dawn" },
        ],
      },
    ]);
    mockGetWorldView.mockResolvedValueOnce({
      id: 1,
      power_system: "Qi",
      world_structure: "Three realms",
      key_factions: [{ name: "Court", description: "Rules the empire", leader: "Emperor", territory: "Capital" }],
      special_rules: "No magic after dusk",
    });
    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    await waitFor(() => {
      expect(screen.getByText("Novel One")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.relationships/ }));
    await waitFor(() => {
      expect(screen.getByText(/Li Wei - Mentor/)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Li Wei - Mentor/ }));
    expect(await screen.findByText("Trust")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.goldenfingers/ }));
    await waitFor(() => {
      expect(screen.getByText("Phoenix Core")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Phoenix Core/ }));
    expect(await screen.findByText("Dormant")).toBeInTheDocument();
    expect(screen.getByText("Sleeping")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.worldview/ }));
    await waitFor(() => {
      expect(screen.getByText("materials:detail.worldviewItem")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /materials:detail.worldviewItem/ }));
    expect(await screen.findByText("Court")).toBeInTheDocument();
    expect(screen.getByText("No magic after dusk")).toBeInTheDocument();
  });

  it("hides disabled stages once their empty counts are confirmed", async () => {
    materialsConfigState.relationshipsEnabled = true;
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Lean Novel",
      status: "completed",
      chapters_count: 1,
      plots_count: 0,
      stories_count: 0,
      story_lines_count: 0,
      relationships_count: 0,
      enabled_stages: {
        chapter_summaries: true,
        plots: false,
        characters: true,
        meta: true,
        synopsis: true,
        stories: false,
        relationships: false,
      },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Lean Novel")).toBeInTheDocument();

    // plots -> 情节点, stories -> 剧情 + 故事线 (snapshot without `storylines`), relationships -> 关系
    const hiddenFolders = [
      /materials:detail.plots/,
      /materials:detail.stories/,
      /materials:detail.storylines/,
      /materials:detail.relationships/,
    ];
    for (const name of hiddenFolders) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(mockGetPlots).not.toHaveBeenCalled();
    expect(mockGetStories).not.toHaveBeenCalled();
    expect(mockGetStoryLines).not.toHaveBeenCalled();
    expect(mockGetRelationships).not.toHaveBeenCalled();

    // Enabled stages still expand and load as before.
    const charactersFolder = screen.getByRole("button", { name: /materials:detail.characters/ });
    expect(charactersFolder).toBeEnabled();
    expect(charactersFolder).not.toHaveTextContent("materials:detail.notEnabled");
    fireEvent.click(charactersFolder);
    expect(await screen.findByText("Li Wei")).toBeInTheDocument();
    expect(screen.queryByText("materials:detail.timeline")).not.toBeInTheDocument();
  });

  it("hides confirmed-empty character and meta folders when those stages were off", async () => {
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Plot Only Novel",
      status: "completed",
      chapters_count: 1,
      characters_count: 0,
      golden_fingers_count: 0,
      has_world_view: false,
      enabled_stages: {
        chapter_summaries: true,
        plots: true,
        characters: false,
        meta: false,
        synopsis: true,
        stories: true,
        relationships: false,
      },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Plot Only Novel")).toBeInTheDocument();

    for (const name of [
      /materials:detail.characters/,
      /materials:detail.goldenfingers/,
      /materials:detail.worldview/,
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    // relationships folder stays hidden while the VITE flag is off
    expect(screen.queryByText("materials:detail.relationships")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /materials:detail.plots/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /materials:detail.stories/ })).toBeEnabled();
  });

  it("maps storylines to its own stage when the snapshot has the storylines key", async () => {
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Stories Only Novel",
      status: "completed",
      chapters_count: 1,
      story_lines_count: 0,
      enabled_stages: {
        chapter_summaries: true,
        plots: true,
        characters: true,
        meta: true,
        synopsis: true,
        stories: true,
        storylines: false,
        relationships: false,
      },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Stories Only Novel")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: /materials:detail.stories/ })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /materials:detail.storylines/ })).not.toBeInTheDocument();
  });

  it("keeps folders with data usable when their stage is now off (retried job)", async () => {
    materialsConfigState.relationshipsEnabled = true;
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Retried Novel",
      status: "completed",
      chapters_count: 1,
      characters_count: 0,
      plots_count: 3,
      stories_count: 2,
      story_lines_count: 0,
      relationships_count: 0,
      golden_fingers_count: 0,
      has_world_view: true,
      enabled_stages: {
        chapter_summaries: true,
        plots: false,
        characters: false,
        meta: false,
        synopsis: true,
        stories: false,
        storylines: false,
        relationships: false,
      },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Retried Novel")).toBeInTheDocument();

    // Stage off but data exists (produced before the stage was switched off): usable.
    for (const name of [
      /materials:detail.plots/,
      /materials:detail.stories/,
      /materials:detail.worldview/,
    ]) {
      const folder = screen.getByRole("button", { name });
      expect(folder).toBeEnabled();
      expect(folder).not.toHaveTextContent("materials:detail.notEnabled");
    }
    // Stage off and confirmed no data: hidden.
    for (const name of [
      /materials:detail.characters/,
      /materials:detail.storylines/,
      /materials:detail.relationships/,
      /materials:detail.goldenfingers/,
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.plots/ }));
    expect(await screen.findByText(/Hidden betrayal/)).toBeInTheDocument();
  });

  it("keeps legacy behavior when the job has no enabled-stage snapshot", async () => {
    materialsConfigState.relationshipsEnabled = true;
    mockGet.mockResolvedValue({
      id: "novel-1",
      title: "Legacy Novel",
      status: "completed",
      chapters_count: 1,
      enabled_stages: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    render(<MaterialDetailPage />, { wrapper: createWrapper() });
    expect(await screen.findByText("Legacy Novel")).toBeInTheDocument();

    expect(screen.queryByText("materials:detail.notEnabled")).not.toBeInTheDocument();
    for (const name of [
      /materials:detail.characters/,
      /materials:detail.stories/,
      /materials:detail.plots/,
      /materials:detail.storylines/,
      /materials:detail.relationships/,
      /materials:detail.goldenfingers/,
      /materials:detail.worldview/,
    ]) {
      expect(screen.getByRole("button", { name })).toBeEnabled();
    }

    fireEvent.click(screen.getByRole("button", { name: /materials:detail.plots/ }));
    await waitFor(() => {
      expect(screen.getByText(/Hidden betrayal/)).toBeInTheDocument();
    });
  });
});

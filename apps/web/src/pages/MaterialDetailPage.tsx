import { useId, useState, type ReactNode } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { materialEnumLabel } from "../lib/materialEnumLabels";
import i18n from "../lib/i18n";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { MaterialReadingContent as MarkdownContent } from "../components/materials/MaterialReadingContent";
import { DashboardSearchBar } from "../components/dashboard/DashboardSearchBar";
import { Button } from "../components/ui/Button";
import { IconButton } from "../components/ui/IconButton";
import { Badge } from "../components/ui/Badge";
import {
  ChevronLeft,
  Folder,
  FolderOpen,
  File,
  User,
  BookOpen,
  Sparkles,
  Globe,
  Zap,
  ChevronRight,
  ChevronDown,
  GitBranch,
  Users,
  Loader2,
  AlertCircle,
  RefreshCw,
} from "../components/icons";
import { materialsApi } from "../lib/materialsApi";
import { ApiError } from "../lib/apiClient";
import type {
  MaterialNovel,
  MaterialEnabledStages,
  MaterialChapter,
  MaterialCharacter,
  MaterialStory,
  MaterialPlot,
  MaterialStoryLine,
  MaterialCharacterRelationship,
  MaterialGoldenFinger,
  MaterialWorldView,
} from "../lib/materialsApi";
import { useIsMobile } from "../hooks/useMediaQuery";
import { materialsConfig } from "../config/materials";
import { handleApiError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { subscriptionApi, subscriptionQueryKeys } from "../lib/subscriptionApi";
import {
  hasMaterialsLibraryAccess,
  isFeatureNotIncludedError,
  materialJobErrorText,
} from "../lib/materialsAccess";
import { MaterialsUpgradeNotice } from "../components/subscription/MaterialsUpgradePrompt";
import { useRefreshMaterialLibraryOnCompletion } from "../hooks/useMaterialLibraryRefresh";


const ACTIVE_MATERIAL_STATUSES = new Set<MaterialNovel["status"]>(["pending", "processing"]);

function isActiveMaterialStatus(status: MaterialNovel["status"]) {
  return ACTIVE_MATERIAL_STATUSES.has(status);
}

type TreeItemType =
  | "folder"
  | "chapter"
  | "character"
  | "story"
  | "worldview"
  | "cheat"
  | "plot"
  | "storyline"
  | "relationship"
  | "goldenfinger";

interface TreeItem {
  id: string;
  type: TreeItemType;
  title: string;
  children?: TreeItem[];
  data?: unknown;
  /** Folder whose decomposition stage was not enabled and that has no data: muted, not expandable. */
  disabled?: boolean;
}

const ITEM_FOLDERS: Partial<Record<TreeItemType, string>> = {
  chapter: "chapters",
  character: "characters",
  story: "stories",
  plot: "plots",
  storyline: "storylines",
  relationship: "relationships",
  goldenfinger: "goldenfingers",
  worldview: "worldview",
};

/**
 * Whether a decomposition stage ran for this material. Jobs created before the
 * enabled-stage snapshot existed report null, which keeps the legacy behavior
 * (treat every stage as enabled).
 */
function isStageEnabled(
  enabledStages: MaterialEnabledStages | null | undefined,
  stage: keyof MaterialEnabledStages,
) {
  return enabledStages?.[stage] !== false;
}

/** Keep historical/unknown folders visible; hide only confirmed-empty disabled stages. */
function shouldShowFolder(stageEnabled: boolean, dataCount: number | boolean | undefined) {
  return stageEnabled || dataCount === undefined || Number(dataCount) > 0;
}

export default function MaterialDetailPage() {
  const { novelId } = useParams<{ novelId: string }>();
  return <MaterialDetailContent key={novelId} novelId={novelId} />;
}

function MaterialDetailContent({ novelId }: { novelId?: string }) {
  const navigate = useNavigate();
  const { t } = useTranslation(["materials", "common"]);
  const isMobile = useIsMobile();

  const [searchQuery, setSearchQuery] = useState("");
  const [selectedItem, setSelectedItem] = useState<TreeItem | null>(null);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  // 移动端：是否显示内容详情（false = 显示文件树）
  const [showMobileContent, setShowMobileContent] = useState(false);

  // Fetch material details
  const {
    data: material,
    isLoading: materialLoading,
    error: materialError,
    refetch: refetchMaterial,
  } = useQuery({
    queryKey: ["material", novelId],
    queryFn: () => materialsApi.get(novelId!),
    enabled: !!novelId,
    staleTime: 30 * 1000,
    refetchInterval: (query) => {
      const data = query.state.data as MaterialNovel | undefined;
      return data && isActiveMaterialStatus(data.status) ? 3000 : false;
    },
  });
  const queryClient = useQueryClient();
  const [isRetrying, setIsRetrying] = useState(false);
  // Retrying charges a monthly breakdown: a free-trial author can only get 402.
  const { data: subscriptionStatus } = useQuery({
    queryKey: subscriptionQueryKeys.status(),
    queryFn: () => subscriptionApi.getStatus(),
  });
  const canRetry =
    hasMaterialsLibraryAccess(
      subscriptionStatus?.features as Record<string, unknown> | undefined,
      subscriptionStatus?.tier,
    ) === true;
  useRefreshMaterialLibraryOnCompletion(material ? [material] : undefined);

  const handleRetry = async () => {
    if (!novelId) return;
    setIsRetrying(true);
    try {
      await materialsApi.retry(novelId);
      toast.success(
        t("materials:retrySuccess", {
          defaultValue: "已重新开始拆解",
        }),
      );
      void queryClient.invalidateQueries({ queryKey: ["materials"] });
      void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
      await refetchMaterial();
    } catch (err) {
      toast.error(handleApiError(err));
    } finally {
      setIsRetrying(false);
    }
  };

  // Progress changes create fresh query identities; late older reads stay in their own cache.
  const materialVersion = material
    ? [
        material.status,
        material.updated_at,
        material.chapters_count,
        material.characters_count,
        material.plots_count,
        material.stories_count,
        material.story_lines_count,
        material.relationships_count,
        material.golden_fingers_count,
        material.has_world_view,
      ].join(":")
    : "";
  const enabledStages = material?.enabled_stages;
  const folderVisibility: Record<string, boolean> = {
    chapters: true,
    characters: shouldShowFolder(isStageEnabled(enabledStages, "characters"), material?.characters_count),
    stories: shouldShowFolder(isStageEnabled(enabledStages, "stories"), material?.stories_count),
    plots: shouldShowFolder(isStageEnabled(enabledStages, "plots"), material?.plots_count),
    storylines: shouldShowFolder(
      isStageEnabled(enabledStages, enabledStages?.storylines === undefined ? "stories" : "storylines"),
      material?.story_lines_count,
    ),
    relationships: (materialsConfig.relationshipsEnabled || Number(material?.relationships_count) > 0)
      && shouldShowFolder(isStageEnabled(enabledStages, "relationships"), material?.relationships_count),
    goldenfingers: shouldShowFolder(isStageEnabled(enabledStages, "meta"), material?.golden_fingers_count),
    worldview: shouldShowFolder(isStageEnabled(enabledStages, "meta"), material?.has_world_view),
  };
  const shouldLoadFolder = (folderId: string) => Boolean(
    novelId && material && folderVisibility[folderId]
    && (expandedFolders.has(folderId) || searchQuery.trim()
      || (selectedItem && ITEM_FOLDERS[selectedItem.type] === folderId)),
  );

  // Fetch chapters
  const chaptersQuery = useQuery({
    queryKey: ["material-chapters", novelId, materialVersion],
    queryFn: async () => {
      const tree = await materialsApi.getTree(novelId!);
      return tree.tree.filter((node) => node.type === "chapter");
    },
    enabled: shouldLoadFolder("chapters"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  // Fetch characters
  const charactersQuery = useQuery({
    queryKey: ["material-characters", novelId, materialVersion],
    queryFn: () => materialsApi.getCharacters(novelId!),
    enabled: shouldLoadFolder("characters"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  // Fetch stories
  const storiesQuery = useQuery({
    queryKey: ["material-stories", novelId, materialVersion],
    queryFn: () => materialsApi.getStories(novelId!),
    enabled: shouldLoadFolder("stories"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  // Fetch plots
  const plotsQuery = useQuery({
    queryKey: ["material-plots", novelId, materialVersion],
    queryFn: () => materialsApi.getPlots(novelId!),
    enabled: shouldLoadFolder("plots"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  // Fetch storylines
  const storylinesQuery = useQuery({
    queryKey: ["material-storylines", novelId, materialVersion],
    queryFn: () => materialsApi.getStoryLines(novelId!),
    enabled: shouldLoadFolder("storylines"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  // Fetch relationships
  const relationshipsQuery = useQuery({
    queryKey: ["material-relationships", novelId, materialVersion],
    queryFn: () => materialsApi.getRelationships(novelId!),
    enabled: shouldLoadFolder("relationships"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  // Fetch golden fingers
  const goldenfingersQuery = useQuery({
    queryKey: ["material-goldenfingers", novelId, materialVersion],
    queryFn: () => materialsApi.getGoldenFingers(novelId!),
    enabled: shouldLoadFolder("goldenfingers"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  // Fetch worldview
  const worldviewQuery = useQuery({
    queryKey: ["material-worldview", novelId, materialVersion],
    queryFn: () => materialsApi.getWorldView(novelId!),
    enabled: shouldLoadFolder("worldview"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  const selectedChapterId = selectedItem?.type === "chapter" ? selectedItem.id : null;
  const selectedChapterQuery = useQuery({
    queryKey: ["material-chapter", novelId, selectedChapterId, materialVersion],
    queryFn: () => materialsApi.getChapter(novelId!, selectedChapterId!),
    enabled: Boolean(novelId && selectedChapterId),
    staleTime: 30 * 1000,
  });

  const { data: selectedChapter, isFetching: isFetchingSelectedChapter } = selectedChapterQuery;

  const { data: chapters = [] } = chaptersQuery;
  const { data: characters = [] } = charactersQuery;
  const { data: stories = [] } = storiesQuery;
  const { data: plots = [] } = plotsQuery;
  const { data: storylines = [] } = storylinesQuery;
  const { data: relationships = [] } = relationshipsQuery;
  const { data: goldenFingers = [] } = goldenfingersQuery;
  const { data: worldview } = worldviewQuery;
  const folderQueries: Record<string, {
    isFetching: boolean;
    isSuccess: boolean;
    isError: boolean;
    refetch: () => Promise<unknown>;
  }> = {
    chapters: chaptersQuery,
    characters: charactersQuery,
    stories: storiesQuery,
    plots: plotsQuery,
    storylines: storylinesQuery,
    relationships: relationshipsQuery,
    goldenfingers: goldenfingersQuery,
    worldview: worldviewQuery,
  };
  const loadingStates = Object.fromEntries(
    Object.entries(folderQueries).map(([id, query]) => [id, query.isFetching]),
  );
  const folderErrors = new Set(
    Object.entries(folderQueries).filter(([, query]) => query.isError).map(([id]) => id),
  );

  // Build tree structure - folders always visible, children lazy loaded
  const buildTree = (): TreeItem[] => {
    const tree: TreeItem[] = [];
    // Chapters folder - always show
    tree.push({
      id: "chapters",
      type: "folder",
      title: t("materials:detail.chapters"),
      children: chapters.map((chapter) => ({
        id: chapter.id,
        type: "chapter",
        title: chapter.title || t("materials:detail.chapter", {
          number: Number(chapter.metadata?.chapter_number ?? 0),
        }),
        data: chapter,
      })),
    });

    if (folderVisibility.characters) tree.push({
      id: "characters",
      type: "folder",
      title: t("materials:detail.characters"),
      children: characters.map((character) => ({
        id: character.id,
        type: "character",
        title: character.name,
        data: character,
      })),
    });

    if (folderVisibility.stories) tree.push({
      id: "stories",
      type: "folder",
      title: t("materials:detail.stories"),
      children: stories.map((story) => ({
        id: story.id,
        type: "story",
        title: story.title,
        data: story,
        metadata: {
          description: story.synopsis,
          plot_type: story.story_type,
        },
      })),
    });

    if (folderVisibility.plots) tree.push({
      id: "plots",
      type: "folder",
      title: t("materials:detail.plots"),
      children: plots.map((plot) => ({
        id: String(plot.id),
        type: "plot",
        title: plot.description.substring(0, 50) + (plot.description.length > 50 ? "…" : ""),
        data: plot,
      })),
    });

    if (folderVisibility.storylines) tree.push({
      id: "storylines",
      type: "folder",
      title: t("materials:detail.storylines"),
      children: storylines.map((storyline) => ({
        id: String(storyline.id),
        type: "storyline",
        title: storyline.title,
        data: storyline,
      })),
    });

    if (folderVisibility.relationships) {
      tree.push({
        id: "relationships",
        type: "folder",
        title: t("materials:detail.relationships"),
        children: relationships.map((rel) => ({
          id: String(rel.id),
          type: "relationship",
          title: `${rel.character_a_name} - ${rel.character_b_name}`,
          data: rel,
        })),
      });
    }

    if (folderVisibility.goldenfingers) tree.push({
      id: "goldenfingers",
      type: "folder",
      title: t("materials:detail.goldenfingers"),
      children: goldenFingers.map((gf) => ({
        id: String(gf.id),
        type: "goldenfinger",
        title: gf.name,
        data: gf,
      })),
    });

    if (folderVisibility.worldview) tree.push({
      id: "worldview",
      type: "folder",
      title: t("materials:detail.worldview"),
      children: worldview ? [{
        id: String(worldview.id),
        type: "worldview",
        title: t("materials:detail.worldviewItem"),
        data: worldview,
      }] : [],
    });

    return tree;
  };

  const treeData = buildTree();
  // Filter tree based on search
  const filterTree = (items: TreeItem[], query: string): TreeItem[] => {
    if (!query) return items;

    return items
      .map((item) => {
        if (item.type === "folder" && item.children) {
          const filteredChildren = filterTree(item.children, query);
          if (filteredChildren.length > 0) {
            return { ...item, children: filteredChildren };
          }
          if (loadingStates[item.id] || !folderQueries[item.id]?.isSuccess) {
            return { ...item, children: [] };
          }
        }

        if (item.title.toLowerCase().includes(query.toLowerCase())) {
          return item;
        }

        return null;
      })
      .filter((item): item is TreeItem => item !== null);
  };

  const filteredTree = filterTree(treeData, searchQuery);

  const toggleFolder = (folderId: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderId)) {
        next.delete(folderId);
      } else {
        next.add(folderId);
      }
      return next;
    });
  };

  const handleItemClick = (item: TreeItem) => {
    if (item.disabled) return;
    if (item.type === "folder") {
      toggleFolder(item.id);
    } else {
      setSelectedItem(item);
      // 移动端：选择项目后切换到内容视图
      if (isMobile) {
        setShowMobileContent(true);
      }
    }
  };

  // Selection keeps its identity; refreshed directory data owns the displayed reference.
  const currentSelectedItem = selectedItem && (
    treeData.flatMap(folder => folder.children ?? []).find(
      item => item.type === selectedItem.type && item.id === selectedItem.id,
    ) ?? selectedItem
  );
  const selectedContentItem = currentSelectedItem?.type === "chapter" && selectedChapter
    ? { ...currentSelectedItem, data: selectedChapter }
    : currentSelectedItem;

  const handleSearchChange = (query: string) => {
    setSearchQuery(query);
    if (query.trim()) {
      Object.entries(folderQueries).forEach(([folderId, folderQuery]) => {
        if (folderVisibility[folderId] && folderQuery.isError) void folderQuery.refetch();
      });
    }
  };

  const handleMobileBack = () => {
    setShowMobileContent(false);
  };

  if (materialLoading) {
    return (
      <div role="status" className="flex items-center justify-center h-dvh">
        <div aria-hidden="true" className="animate-spin rounded-full h-8 w-8 border-b-2 border-[hsl(var(--accent-primary))]" />
        <span className="sr-only">{t("common:loading", { defaultValue: "加载中…" })}</span>
      </div>
    );
  }

  if (materialError && isFeatureNotIncludedError(materialError)) {
    return (
      <div className="flex h-dvh items-center justify-center px-4">
        <MaterialsUpgradeNotice source="material_detail" className="max-w-md text-center" />
      </div>
    );
  }

  if (materialError) {
    const isNotFound = materialError instanceof ApiError && materialError.status === 404;
    return (
      <div className="flex flex-col items-center justify-center gap-3 h-dvh">
        <p role="alert" className="text-[hsl(var(--text-secondary))]">
          {isNotFound
            ? t("materials:detail.notFound")
            : t("materials:detail.loadError", { defaultValue: "素材详情加载失败，请重试。" })}
        </p>
        {!isNotFound && (
          <Button variant="secondary" size="touch" onClick={() => void refetchMaterial()}>
            {t("common:retry", { defaultValue: "重试" })}
          </Button>
        )}
      </div>
    );
  }

  if (!material) {
    return (
      <div className="flex items-center justify-center h-dvh">
        <p className="text-[hsl(var(--text-secondary))]">{t("materials:detail.notFound")}</p>
      </div>
    );
  }

  return (
    <div className="viewport-shell ui-sans-headings h-dvh flex flex-col overflow-hidden bg-[hsl(var(--bg-primary))]">
      <header className="shrink-0 border-b border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))]">
        <div className={`flex gap-3 ${isMobile ? "flex-col px-3 py-3" : "items-center justify-between px-5 py-3"}`}>
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <IconButton
              size="touch"
              label={t("common:back", { defaultValue: "返回" })}
              icon={<ChevronLeft className="h-5 w-5" />}
              onClick={() => {
                if (isMobile && showMobileContent) handleMobileBack();
                else navigate("/dashboard/materials");
              }}
            />
            <div className="min-w-0">
              <h1 id="material-title" className="heading-display break-words text-xl font-semibold tracking-tight text-[hsl(var(--text-primary))] md:text-2xl">
                {material.title}
              </h1>
              <p className="mt-0.5 text-xs text-[hsl(var(--text-secondary))]">
                {material.chapters_count || 0} {t("materials:chapters")}
              </p>
            </div>
          </div>
          {!(isMobile && showMobileContent) && (
            <DashboardSearchBar
              value={searchQuery}
              onChange={handleSearchChange}
              placeholder={t("materials:detail.searchPlaceholder")}
              className={isMobile ? "w-full" : "w-56 shrink-0 lg:w-64"}
            />
          )}
        </div>
      </header>

      {(material.status === "failed" || material.status === "completed_with_errors") && (
        <div
          role="alert"
          className={`shrink-0 flex flex-wrap items-center gap-3 border-b px-4 py-2.5 text-sm ${
            material.status === "failed"
              ? "border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] text-[hsl(var(--error-text))]"
              : "border-[hsl(var(--warning)/0.3)] bg-[hsl(var(--warning)/0.08)] text-[hsl(var(--warning-text))]"
          }`}
        >
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1 min-w-0">
            <span className="font-medium">
              {material.status === "failed"
                ? t("materials:detail.failedBanner", { defaultValue: "拆解失败" })
                : t("materials:detail.partialBanner", { defaultValue: "部分内容没有拆出来" })}
            </span>
            {material.error_message && (
              <span className="ml-2 text-[hsl(var(--text-secondary))]">
                {materialJobErrorText(material.error_message)}
              </span>
            )}
          </span>
          {canRetry && (
            <Button
              variant="secondary"
              size={isMobile ? "touch" : "sm"}
              onClick={() => void handleRetry()}
              isLoading={isRetrying}
              leftIcon={<RefreshCw className="h-3.5 w-3.5" />}
            >
              {t("common:retry", { defaultValue: "重试" })}
            </Button>
          )}
        </div>
      )}

      <main aria-labelledby="material-title" className="flex min-h-0 flex-1 overflow-hidden">
        {(!isMobile || !showMobileContent) && (
          <aside
            aria-label={t("materials:detail.contents")}
            className={`${isMobile ? "flex-1" : "w-64 shrink-0 border-r lg:w-72"} overflow-y-auto border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-primary))]`}
          >
            <div className="px-3 py-5">
              <h2 className="mb-3 px-2 text-xs font-medium text-[hsl(var(--text-secondary))]">
                {t("materials:detail.contents")}
              </h2>
              <FileTree
                items={filteredTree}
                selectedId={selectedItem?.id}
                expandedFolders={expandedFolders}
                onItemClick={handleItemClick}
                loadingStates={loadingStates}
                folderErrors={folderErrors}
                onRetryFolder={(folderId) => { void folderQueries[folderId]?.refetch(); }}
                searchActive={Boolean(searchQuery.trim())}
              />
            </div>
          </aside>
        )}
        {(!isMobile || showMobileContent) && (
          <section
            aria-label={t("materials:detail.contentLabel")}
            className="min-w-0 flex-1 overflow-y-auto bg-[hsl(var(--bg-secondary))]"
          >
            <div className="mx-auto w-full max-w-[50rem] px-5 py-7 md:px-8 md:py-10 lg:px-12">
              {selectedContentItem ? (
                isFetchingSelectedChapter && selectedContentItem.type === "chapter" ? (
                  <div role="status" aria-label={t("common:loading")} className="flex min-h-40 items-center justify-center">
                    <Loader2 className="h-5 w-5 animate-spin text-[hsl(var(--text-secondary))]" />
                  </div>
                ) : selectedContentItem.type === "chapter" && selectedChapterQuery.isError && !selectedChapter ? (
                  <div role="alert" className="space-y-3 py-8 text-sm text-[hsl(var(--text-secondary))]">
                    <p>{t("materials:detail.loadError", { defaultValue: "素材详情加载失败，请重试。" })}</p>
                    <Button variant="secondary" size={isMobile ? "touch" : "md"} onClick={() => void selectedChapterQuery.refetch()}>
                      {t("common:retry", { defaultValue: "重试" })}
                    </Button>
                  </div>
                ) : <ContentDetail item={selectedContentItem} />
              ) : <EmptyState material={material} />}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

// FileTree Component
interface FileTreeProps {
  items: TreeItem[];
  selectedId?: string;
  expandedFolders: Set<string>;
  onItemClick: (item: TreeItem) => void;
  loadingStates: Record<string, boolean>;
  folderErrors: Set<string>;
  onRetryFolder: (folderId: string) => void;
  searchActive?: boolean;
  level?: number;
}

function FileTree({ items, selectedId, expandedFolders, onItemClick, loadingStates, folderErrors, onRetryFolder, searchActive = false, level = 0 }: FileTreeProps) {
  const { t } = useTranslation(["materials", "common"]);
  const getIcon = (type: TreeItemType, isExpanded: boolean) => {
    switch (type) {
      case "folder":
        return isExpanded ? (
          <FolderOpen className="w-4 h-4 text-current" />
        ) : (
          <Folder className="w-4 h-4 text-[hsl(var(--text-tertiary))]" />
        );
      case "chapter":
        return <BookOpen className="w-4 h-4 text-[hsl(var(--text-secondary))]" />;
      case "character":
        return <User className="w-4 h-4 text-[hsl(var(--text-secondary))]" />;
      case "story":
        return <Sparkles className="w-4 h-4 text-[hsl(var(--text-secondary))]" />;
      case "worldview":
        return <Globe className="w-4 h-4 text-[hsl(var(--text-secondary))]" />;
      case "cheat":
        return <Zap className="w-4 h-4 text-[hsl(var(--text-secondary))]" />;
      case "plot":
        return <File className="w-4 h-4 text-[hsl(var(--text-secondary))]" />;
      case "storyline":
        return <GitBranch className="w-4 h-4 text-[hsl(var(--text-secondary))]" />;
      case "relationship":
        return <Users className="w-4 h-4 text-[hsl(var(--text-secondary))]" />;
      case "goldenfinger":
        return <Zap className="w-4 h-4 text-[hsl(var(--text-secondary))]" />;
      default:
        return <File className="w-4 h-4 text-[hsl(var(--text-tertiary))]" />;
    }
  };

  return (
    <div className="space-y-1">
      {items.map((item) => {
        const isDisabled = Boolean(item.disabled);
        const isExpanded = !isDisabled && (searchActive || expandedFolders.has(item.id));
        const isSelected = selectedId === item.id;
        const hasChildren = item.children && item.children.length > 0;

        return (
          <div key={item.id}>
            <button
              onClick={() => onItemClick(item)}
              disabled={isDisabled}
              aria-disabled={isDisabled || undefined}
              aria-expanded={item.type === "folder" ? isExpanded : undefined}
              aria-current={isSelected ? "true" : undefined}
              title={item.title}
              className={`w-full flex items-center gap-2 px-2 py-2 min-h-10 max-md:min-h-11 rounded-lg text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)] ${
                isDisabled
                  ? "cursor-not-allowed text-[hsl(var(--text-tertiary))]"
                  : isSelected
                  ? "bg-[hsl(var(--accent-primary)/0.08)] text-[hsl(var(--accent-primary))]"
                  : "hover:bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))] active:bg-[hsl(var(--bg-hover))]"
              }`}
              style={{ paddingLeft: `${level * 12 + 8}px` }}
            >
              {item.type === "folder" && isDisabled && <span className="shrink-0 w-3.5" />}
              {item.type === "folder" && !isDisabled && (
                <span className="shrink-0">
                  {isExpanded ? (
                    <ChevronDown className="w-3.5 h-3.5" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5" />
                  )}
                </span>
              )}
              <span className="shrink-0">{getIcon(item.type, isExpanded)}</span>
              <span className="truncate flex-1 text-left">{item.title}</span>
              {isDisabled && (
                <span className="shrink-0 text-xs text-[hsl(var(--text-tertiary))]">
                  {t("materials:detail.notEnabled")}
                </span>
              )}
              {item.type === "folder" && loadingStates[item.id] && (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-[hsl(var(--accent-primary))]" />
              )}
              {hasChildren && (
                <span className="text-xs text-[hsl(var(--text-tertiary))]">
                  {item.children!.length}
                </span>
              )}
            </button>

            {item.type === "folder" && isExpanded && folderErrors.has(item.id) && (
              <div role="alert" className="ml-8 space-y-2 py-2 text-xs text-[hsl(var(--error-text))]">
                <p>{t("materials:detail.folderLoadError", { defaultValue: "加载失败。" })}</p>
                <Button variant="secondary" size="touch" onClick={() => onRetryFolder(item.id)}>
                  {t("common:retry", { defaultValue: "重试" })}
                </Button>
              </div>
            )}
            {item.type === "folder" && isExpanded && hasChildren && !folderErrors.has(item.id) && (
              <FileTree
                items={item.children!}
                selectedId={selectedId}
                expandedFolders={expandedFolders}
                onItemClick={onItemClick}
                loadingStates={loadingStates}
                folderErrors={folderErrors}
                onRetryFolder={onRetryFolder}
                searchActive={searchActive}
                level={level + 1}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// Shared structure for the seven reference types; chapter layout stays independent.
function DetailHeader({ id, icon, title, children }: { id: string; icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <header className="material-analysis-header">
      <div className="flex items-start gap-3">
        <span className="mt-1.5 shrink-0 text-[hsl(var(--text-secondary))]" aria-hidden="true">{icon}</span>
        <h2 id={id} className="text-2xl font-semibold leading-snug tracking-tight text-[hsl(var(--text-primary))]">{title}</h2>
      </div>
      {children}
    </header>
  );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section className="material-analysis-section" aria-labelledby={id}>
      <h3 id={id}>{title}</h3>
      {children}
    </section>
  );
}

function DetailMetadata({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="material-analysis-fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function DetailTags({ values }: { values: string[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {values.map((value, index) => <Badge key={index} variant="neutral" size="md">{value}</Badge>)}
    </div>
  );
}

// ContentDetail Component
interface ContentDetailProps {
  item: TreeItem;
}

function ContentDetail({ item }: ContentDetailProps) {
  const { t } = useTranslation(["materials"]);
  const titleId = useId();

  if (item.type === "chapter") {
    const chapter = item.data as MaterialChapter;
    return (
      <article className="material-detail" aria-labelledby="material-chapter-title">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <BookOpen className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
            <h2 id="material-chapter-title" className="heading-display text-2xl font-semibold leading-snug tracking-tight text-[hsl(var(--text-primary))] md:text-[28px]">
              {chapter.title}
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[hsl(var(--text-secondary))]">
            <span>{t("materials:detail.chapterNumber")}: {chapter.chapter_number}</span>
            <span>{t("materials:detail.wordCount")}: {chapter.word_count?.toLocaleString()}</span>
          </div>
        </div>

        {chapter.summary && (
          <div className="mb-8 rounded-xl border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-primary))] p-4">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.summary")}
            </h3>
            <MarkdownContent content={chapter.summary} />
          </div>
        )}

        {chapter.content && (
          <MarkdownContent content={chapter.content} className="material-reader-manuscript" />
        )}
      </article>
    );
  }

  if (item.type === "character") {
    const character = item.data as MaterialCharacter;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<User className="w-5 h-5" />} title={character.name}>
          <dl className="material-analysis-facts">
            {character.archetype && (
              <DetailMetadata label={t("materials:detail.characterArchetype")}>
                {t(`materials:enums.archetype.${character.archetype.trim().toLowerCase()}`, { defaultValue: character.archetype })}
              </DetailMetadata>
            )}
            {!!character.aliases?.length && (
              <DetailMetadata label={t("materials:detail.aliases")}>
                {character.aliases.join(i18n.language === 'zh' ? '、' : ', ')}
              </DetailMetadata>
            )}
            {character.first_appearance_chapter != null && (
              <DetailMetadata label={t("materials:detail.firstAppearance")}>
                {t("materials:detail.chapter", { number: character.first_appearance_chapter })}
              </DetailMetadata>
            )}
          </dl>
        </DetailHeader>
        {character.description && (
          <DetailSection title={t("materials:detail.characterOverview")}>
            <MarkdownContent content={character.description} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "story") {
    const story = item.data as MaterialStory;
    const parseThemes = (themes: string | null | undefined): string[] => {
      if (!themes) return [];
      try {
        const parsed = JSON.parse(themes);
        return Array.isArray(parsed) ? parsed : [themes];
      } catch {
        return themes.split(',').map(theme => theme.trim()).filter(Boolean);
      }
    };
    const themes = parseThemes(story.themes);
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<Sparkles className="w-5 h-5" />} title={story.title}>
          {story.story_type && <Badge variant="neutral" size="md">{materialEnumLabel(t, 'storyType', story.story_type)}</Badge>}
          {story.chapter_range && (
            <dl className="material-analysis-facts">
              <DetailMetadata label={t("materials:detail.relatedChapters")}>
                {t("materials:detail.chapterRange", { range: story.chapter_range })}
              </DetailMetadata>
            </dl>
          )}
        </DetailHeader>
        {story.synopsis && (
          <DetailSection title={t("materials:detail.storySynopsis")}>
            <MarkdownContent content={story.synopsis} />
          </DetailSection>
        )}
        {story.core_objective && (
          <DetailSection title={t("materials:detail.coreObjective")}>
            <MarkdownContent content={story.core_objective} />
          </DetailSection>
        )}
        {story.core_conflict && (
          <DetailSection title={t("materials:detail.coreConflict")}>
            <MarkdownContent content={story.core_conflict} />
          </DetailSection>
        )}
        {!!themes.length && (
          <DetailSection title={t("materials:detail.themes")}>
            <DetailTags values={themes} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "plot") {
    const plot = item.data as MaterialPlot;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<File className="w-5 h-5" />} title={t("materials:detail.plotTitle")}>
          <Badge variant="neutral" size="md">{materialEnumLabel(t, 'plotType', plot.plot_type)}</Badge>
        </DetailHeader>
        {plot.description && (
          <DetailSection title={t("materials:detail.plotEvent")}>
            <MarkdownContent content={plot.description} />
          </DetailSection>
        )}
        {!!plot.characters?.length && (
          <DetailSection title={t("materials:detail.involvedCharacters")}>
            <DetailTags values={plot.characters} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "storyline") {
    const storyline = item.data as MaterialStoryLine;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<GitBranch className="w-5 h-5" />} title={storyline.title}>
          <dl className="material-analysis-facts">
            <DetailMetadata label={t("materials:detail.storiesCount")}>{storyline.stories_count}</DetailMetadata>
          </dl>
        </DetailHeader>
        {storyline.description && (
          <DetailSection title={t("materials:detail.storylineOverview")}>
            <MarkdownContent content={storyline.description} />
          </DetailSection>
        )}
        {!!storyline.main_characters?.length && (
          <DetailSection title={t("materials:detail.mainCharacters")}>
            <DetailTags values={storyline.main_characters} />
          </DetailSection>
        )}
        {!!storyline.themes?.length && (
          <DetailSection title={t("materials:detail.themes")}>
            <DetailTags values={storyline.themes} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "relationship") {
    const relationship = item.data as MaterialCharacterRelationship;
    // Known values are localized; free-form historical descriptions remain intact.
    const sentiment = relationship.sentiment && t(`materials:enums.sentiment.${relationship.sentiment.trim().toLowerCase()}`, { defaultValue: relationship.sentiment });
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<Users className="w-5 h-5" />} title={`${relationship.character_a_name} - ${relationship.character_b_name}`}>
          <Badge variant="neutral" size="md">{materialEnumLabel(t, 'relationshipType', relationship.relationship_type)}</Badge>
        </DetailHeader>
        {relationship.description && (
          <DetailSection title={t("materials:detail.relationshipDescription")}>
            <MarkdownContent content={relationship.description} />
          </DetailSection>
        )}
        {sentiment && (
          <DetailSection title={t("materials:detail.sentiment")}>
            <MarkdownContent content={sentiment} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "goldenfinger") {
    const goldenfinger = item.data as MaterialGoldenFinger;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<Zap className="w-5 h-5" />} title={goldenfinger.name}>
          <Badge variant="neutral" size="md">{materialEnumLabel(t, 'goldenFingerType', goldenfinger.type)}</Badge>
        </DetailHeader>
        {goldenfinger.description && (
          <DetailSection title={t("materials:detail.abilityDescription")}>
            <MarkdownContent content={goldenfinger.description} />
          </DetailSection>
        )}
        {!!goldenfinger.evolution_history?.length && (
          <DetailSection title={t("materials:detail.evolutionHistory")}>
            <ol className="material-analysis-records" role="list">
              {goldenfinger.evolution_history.map((record, index) => (
                <li key={index}>
                  {record.stage && <h4>{record.stage}</h4>}
                  {(record.chapter != null || record.timestamp) && <dl className="material-analysis-facts">
                    {record.chapter != null && (
                      <DetailMetadata label={t("materials:detail.chapterLabel")}>
                        {record.chapter}
                      </DetailMetadata>
                    )}
                    {record.timestamp && (
                      <DetailMetadata label={t("materials:detail.timeLabel")}>
                        {record.timestamp}
                      </DetailMetadata>
                    )}
                  </dl>}
                  {record.description && <MarkdownContent content={record.description} />}
                </li>
              ))}
            </ol>
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "worldview") {
    const worldview = item.data as MaterialWorldView;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<Globe className="w-5 h-5" />} title={t("materials:detail.worldview")} />
        {worldview.power_system && (
          <DetailSection title={t("materials:detail.powerSystem")}>
            <MarkdownContent content={worldview.power_system} />
          </DetailSection>
        )}
        {worldview.world_structure && (
          <DetailSection title={t("materials:detail.worldStructure")}>
            <MarkdownContent content={worldview.world_structure} />
          </DetailSection>
        )}
        {!!worldview.key_factions?.length && (
          <DetailSection title={t("materials:detail.keyFactions")}>
            <ul className="material-analysis-records" role="list">
              {worldview.key_factions.map((faction, index) => (
                <li key={index}>
                  <h4>{faction.name}</h4>
                  {faction.description && <MarkdownContent content={faction.description} />}
                  {(faction.leader || faction.territory) && <dl className="material-analysis-facts">
                    {faction.leader && (
                      <DetailMetadata label={t("materials:detail.leader")}>
                        {faction.leader}
                      </DetailMetadata>
                    )}
                    {faction.territory && (
                      <DetailMetadata label={t("materials:detail.territory")}>
                        {faction.territory}
                      </DetailMetadata>
                    )}
                  </dl>}
                </li>
              ))}
            </ul>
          </DetailSection>
        )}
        {worldview.special_rules && (
          <DetailSection title={t("materials:detail.specialRules")}>
            <MarkdownContent content={worldview.special_rules} />
          </DetailSection>
        )}
      </article>
    );
  }

  return null;
}

// EmptyState Component
interface EmptyStateProps {
  material: MaterialNovel;
}

function EmptyState({ material }: EmptyStateProps) {
  const { t } = useTranslation(["materials"]);

  return (
    <div className="flex flex-col items-center justify-center h-full text-center py-12">
      <div className="w-12 h-12 rounded-xl bg-[hsl(var(--bg-tertiary))] flex items-center justify-center mb-4">
        <BookOpen className="w-6 h-6 text-[hsl(var(--text-secondary))]" />
      </div>
      <h3 className="text-base font-semibold text-[hsl(var(--text-primary))] mb-2">
        {t("materials:detail.emptyTitle")}
      </h3>
      <p className="text-sm text-[hsl(var(--text-secondary))] max-w-md">
        {t("materials:detail.emptyDescription")}
      </p>

      {isActiveMaterialStatus(material.status) && (
        <div className="mt-6 flex items-center gap-2 text-sm text-[hsl(var(--text-secondary))]">
          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-[hsl(var(--text-secondary))]" />
          <span>{t(`materials:status.${material.status}`)}</span>
        </div>
      )}
    </div>
  );
}

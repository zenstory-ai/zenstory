import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { materialEnumLabel } from "../lib/materialEnumLabels";
import i18n from "../lib/i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LazyMarkdown } from "../components/LazyMarkdown";
import {
  ChevronLeft,
  Search,
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
import { subscriptionQueryKeys } from "../lib/subscriptionApi";
import { isFeatureNotIncludedError, materialJobErrorText } from "../lib/materialsAccess";
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
  const [loadedFolders, setLoadedFolders] = useState<Set<string>>(new Set());
  const [folderErrors, setFolderErrors] = useState<Set<string>>(new Set());
  // 移动端：是否显示内容详情（false = 显示文件树）
  const [showMobileContent, setShowMobileContent] = useState(false);
  const activeNovelIdRef = useRef(novelId);

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

  // Fetch chapters
  const { data: chapters = [], refetch: refetchChapters, isFetching: isFetchingChapters } = useQuery({
    queryKey: ["material-chapters", novelId],
    queryFn: async () => {
      const tree = await materialsApi.getTree(novelId!);
      return tree.tree.filter((node) => node.type === "chapter");
    },
    enabled: false,
    staleTime: 30 * 1000,
  });

  // Fetch characters
  const { data: characters = [], refetch: refetchCharacters, isFetching: isFetchingCharacters } = useQuery({
    queryKey: ["material-characters", novelId],
    queryFn: () => materialsApi.getCharacters(novelId!),
    enabled: false,
    staleTime: 30 * 1000,
  });

  // Fetch stories
  const { data: stories = [], refetch: refetchStories, isFetching: isFetchingStories } = useQuery({
    queryKey: ["material-stories", novelId],
    queryFn: () => materialsApi.getStories(novelId!),
    enabled: false,
    staleTime: 30 * 1000,
  });

  // Fetch plots
  const { data: plots = [], refetch: refetchPlots, isFetching: isFetchingPlots } = useQuery({
    queryKey: ["material-plots", novelId],
    queryFn: () => materialsApi.getPlots(novelId!),
    enabled: false,
    staleTime: 30 * 1000,
  });

  // Fetch storylines
  const { data: storylines = [], refetch: refetchStorylines, isFetching: isFetchingStorylines } = useQuery({
    queryKey: ["material-storylines", novelId],
    queryFn: () => materialsApi.getStoryLines(novelId!),
    enabled: false,
    staleTime: 30 * 1000,
  });

  // Fetch relationships
  const { data: relationships = [], refetch: refetchRelationships, isFetching: isFetchingRelationships } = useQuery({
    queryKey: ["material-relationships", novelId],
    queryFn: () => materialsApi.getRelationships(novelId!),
    enabled: false,
    staleTime: 30 * 1000,
  });

  // Fetch golden fingers
  const { data: goldenFingers = [], refetch: refetchGoldenFingers, isFetching: isFetchingGoldenFingers } = useQuery({
    queryKey: ["material-goldenfingers", novelId],
    queryFn: () => materialsApi.getGoldenFingers(novelId!),
    enabled: false,
    staleTime: 30 * 1000,
  });

  // Fetch worldview
  const { data: worldview, refetch: refetchWorldview, isFetching: isFetchingWorldview } = useQuery({
    queryKey: ["material-worldview", novelId],
    queryFn: () => materialsApi.getWorldView(novelId!),
    enabled: false,
    staleTime: 30 * 1000,
  });

  const selectedChapterId = selectedItem?.type === "chapter" ? selectedItem.id : null;
  const { data: selectedChapter, isFetching: isFetchingSelectedChapter } = useQuery({
    queryKey: ["material-chapter", novelId, selectedChapterId],
    queryFn: () => materialsApi.getChapter(novelId!, selectedChapterId!),
    enabled: Boolean(novelId && selectedChapterId),
    staleTime: 30 * 1000,
  });

  // Loading states mapping
  const loadingStates: Record<string, boolean> = {
    chapters: isFetchingChapters,
    characters: isFetchingCharacters,
    stories: isFetchingStories,
    plots: isFetchingPlots,
    storylines: isFetchingStorylines,
    relationships: isFetchingRelationships,
    goldenfingers: isFetchingGoldenFingers,
    worldview: isFetchingWorldview,
  };

  // Trigger load function
  const triggerLoad = useCallback((folderId: string, force = false) => {
    const isFolderLoading = {
      chapters: isFetchingChapters,
      characters: isFetchingCharacters,
      stories: isFetchingStories,
      plots: isFetchingPlots,
      storylines: isFetchingStorylines,
      relationships: isFetchingRelationships,
      goldenfingers: isFetchingGoldenFingers,
      worldview: isFetchingWorldview,
    }[folderId];
    if ((!force && loadedFolders.has(folderId)) || isFolderLoading) return;
    const requestNovelId = novelId;
    const refetchByFolder: Record<string, () => Promise<{ status: string }>> = {
      chapters: refetchChapters,
      characters: refetchCharacters,
      stories: refetchStories,
      plots: refetchPlots,
      storylines: refetchStorylines,
      relationships: refetchRelationships,
      goldenfingers: refetchGoldenFingers,
      worldview: refetchWorldview,
    };

    const refetchFolder = refetchByFolder[folderId];
    if (!refetchFolder) return;
    setFolderErrors((prev) => {
      const next = new Set(prev);
      next.delete(folderId);
      return next;
    });

    void refetchFolder().then((result) => {
      if (activeNovelIdRef.current !== requestNovelId) return;
      if (result.status !== "success") {
        setFolderErrors((prev) => new Set(prev).add(folderId));
        setLoadedFolders((prev) => {
          const next = new Set(prev);
          next.delete(folderId);
          return next;
        });
        return;
      }
      setFolderErrors((prev) => {
        const next = new Set(prev);
        next.delete(folderId);
        return next;
      });
      setLoadedFolders((prev) => new Set(prev).add(folderId));
    });
  }, [
    isFetchingChapters,
    isFetchingCharacters,
    isFetchingGoldenFingers,
    isFetchingPlots,
    isFetchingRelationships,
    isFetchingStories,
    isFetchingStorylines,
    isFetchingWorldview,
    loadedFolders,
    novelId,
    refetchChapters,
    refetchCharacters,
    refetchGoldenFingers,
    refetchPlots,
    refetchRelationships,
    refetchStories,
    refetchStorylines,
    refetchWorldview,
  ]);

  // Build tree structure - folders always visible, children lazy loaded
  const buildTree = (): TreeItem[] => {
    const tree: TreeItem[] = [];
    const enabledStages = material?.enabled_stages;
    const charactersEnabled = isStageEnabled(enabledStages, "characters");
    const storiesEnabled = isStageEnabled(enabledStages, "stories");
    // Older snapshots have no `storylines` key: storylines followed `stories` then.
    const storylinesEnabled = isStageEnabled(
      enabledStages,
      enabledStages?.storylines === undefined ? "stories" : "storylines",
    );
    const plotsEnabled = isStageEnabled(enabledStages, "plots");
    const relationshipsEnabled = isStageEnabled(enabledStages, "relationships");
    const metaEnabled = isStageEnabled(enabledStages, "meta");

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

    if (shouldShowFolder(charactersEnabled, material?.characters_count)) tree.push({
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

    if (shouldShowFolder(storiesEnabled, material?.stories_count)) tree.push({
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

    if (shouldShowFolder(plotsEnabled, material?.plots_count)) tree.push({
      id: "plots",
      type: "folder",
      title: t("materials:detail.plots"),
      children: plots.map((plot) => ({
        id: String(plot.id),
        type: "plot",
        title: plot.description.substring(0, 50) + (plot.description.length > 50 ? "..." : ""),
        data: plot,
      })),
    });

    if (shouldShowFolder(storylinesEnabled, material?.story_lines_count)) tree.push({
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

    if (
      (materialsConfig.relationshipsEnabled || Number(material?.relationships_count) > 0)
      && shouldShowFolder(relationshipsEnabled, material?.relationships_count)
    ) {
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

    if (shouldShowFolder(metaEnabled, material?.golden_fingers_count)) tree.push({
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

    if (shouldShowFolder(metaEnabled, material?.has_world_view)) tree.push({
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
  const visibleFolderIds = treeData
    .filter((item) => item.type === "folder")
    .map((item) => item.id);
  const visibleFolderKey = visibleFolderIds.join(":");
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
  const previousMaterialRef = useRef<{ novelId?: string; version: string } | undefined>(undefined);

  useEffect(() => {
    const previous = previousMaterialRef.current;
    previousMaterialRef.current = { novelId, version: materialVersion };
    if (!previous || previous.novelId !== novelId || previous.version === materialVersion) return;

    const currentVisibleFolderIds = visibleFolderKey ? visibleFolderKey.split(":") : [];
    const foldersToReload = searchQuery.trim()
      ? currentVisibleFolderIds
      : currentVisibleFolderIds.filter((folderId) => expandedFolders.has(folderId));
    foldersToReload.forEach((folderId) => triggerLoad(folderId, true));
  }, [
    expandedFolders,
    materialVersion,
    novelId,
    searchQuery,
    triggerLoad,
    visibleFolderKey,
  ]);

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
          if (loadingStates[item.id] || !loadedFolders.has(item.id)) {
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
        triggerLoad(folderId);
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

  const selectedContentItem = selectedItem?.type === "chapter" && selectedChapter
    ? { ...selectedItem, data: selectedChapter }
    : selectedItem;

  const handleMobileBack = () => {
    setShowMobileContent(false);
  };

  if (materialLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[hsl(var(--accent-primary))]" />
      </div>
    );
  }

  if (materialError && isFeatureNotIncludedError(materialError)) {
    return (
      <div className="flex h-screen items-center justify-center px-4">
        <MaterialsUpgradeNotice source="material_detail" className="max-w-md text-center" />
      </div>
    );
  }

  if (materialError) {
    const isNotFound = materialError instanceof ApiError && materialError.status === 404;
    return (
      <div className="flex flex-col items-center justify-center gap-3 h-screen">
        <p className="text-[hsl(var(--text-secondary))]">
          {isNotFound
            ? t("materials:detail.notFound")
            : t("materials:detail.loadError", { defaultValue: "素材详情加载失败，请重试。" })}
        </p>
        {!isNotFound && (
          <button className="btn-secondary h-10 px-4" onClick={() => void refetchMaterial()}>
            {t("common:retry", { defaultValue: "重试" })}
          </button>
        )}
      </div>
    );
  }

  if (!material) {
    return (
      <div className="flex items-center justify-center h-screen">
        <p className="text-[hsl(var(--text-secondary))]">{t("materials:detail.notFound")}</p>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-[hsl(var(--bg-primary))]">
      {/* Header */}
      <div className="shrink-0 border-b border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))]">
        <div className={`flex ${isMobile ? 'flex-col items-stretch gap-2 px-3 py-2' : 'items-center justify-between px-4 py-3'}`}>
          <div className="flex items-center gap-3">
            <button
              onClick={() => {
                if (isMobile && showMobileContent) {
                  handleMobileBack();
                } else {
                  navigate("/dashboard/materials");
                }
              }}
              className="p-2 rounded-lg hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
            >
              <ChevronLeft className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
            </button>
            <div>
              <h1 className={`font-semibold text-[hsl(var(--text-primary))] ${isMobile ? 'text-base' : 'text-lg'}`}>
                {isMobile && showMobileContent && selectedItem ? selectedItem.title : material.title}
              </h1>
              {!(isMobile && showMobileContent) && (
                <p className="text-xs text-[hsl(var(--text-tertiary))]">
                  {material.chapters_count || 0} {t("materials:chapters")}
                </p>
              )}
            </div>
          </div>

          {/* Search - 在移动端内容视图隐藏 */}
          {!(isMobile && showMobileContent) && (
            <div className={`relative ${isMobile ? 'w-full' : 'max-w-xs'}`}>
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[hsl(var(--text-tertiary))]" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => {
                  const query = e.target.value;
                  setSearchQuery(query);
                  if (query.trim()) {
                    visibleFolderIds.forEach((folderId) => triggerLoad(folderId));
                  }
                }}
                placeholder={t("materials:detail.searchPlaceholder")}
                className={`w-full pl-9 pr-3 py-2 rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.3)]`}
              />
            </div>
          )}
        </div>
      </div>

      {(material.status === "failed" || material.status === "completed_with_errors") && (
        <div
          role="alert"
          className={`shrink-0 flex flex-wrap items-center gap-3 border-b px-4 py-2.5 text-sm ${
            material.status === "failed"
              ? "border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] text-[hsl(var(--error))]"
              : "border-[hsl(var(--warning)/0.3)] bg-[hsl(var(--warning)/0.08)] text-[hsl(var(--warning))]"
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
          <button
            type="button"
            onClick={() => void handleRetry()}
            disabled={isRetrying}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] px-3 text-xs font-medium text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isRetrying ? "animate-spin" : ""}`} />
            {t("common:retry", { defaultValue: "重试" })}
          </button>
        </div>
      )}

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {isMobile ? (
          // 移动端：单栏切换布局
          showMobileContent ? (
            // 内容详情视图
            <div className="flex-1 overflow-y-auto">
              <div className="p-4">
                {selectedContentItem ? (
                  isFetchingSelectedChapter && selectedContentItem.type === "chapter" ? (
                    <Loader2 className="w-5 h-5 animate-spin text-[hsl(var(--accent-primary))]" />
                  ) : <ContentDetail item={selectedContentItem} />
                ) : (
                  <EmptyState material={material} />
                )}
              </div>
            </div>
          ) : (
            // 文件树视图
            <div className="flex-1 overflow-y-auto bg-[hsl(var(--bg-secondary))]">
              <div className="p-3">
                <FileTree
                  items={filteredTree}
                  selectedId={selectedItem?.id}
                  expandedFolders={expandedFolders}
                  onItemClick={handleItemClick}
                  loadingStates={loadingStates}
                  folderErrors={folderErrors}
                  onRetryFolder={(folderId) => triggerLoad(folderId, true)}
                  searchActive={Boolean(searchQuery.trim())}
                />
              </div>
            </div>
          )
        ) : (
          // 桌面端：双栏布局
          <>
            {/* Left: File Tree */}
            <div className="w-80 border-r border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] overflow-y-auto">
              <div className="p-4">
                <FileTree
                  items={filteredTree}
                  selectedId={selectedItem?.id}
                  expandedFolders={expandedFolders}
                  onItemClick={handleItemClick}
                  loadingStates={loadingStates}
                  folderErrors={folderErrors}
                  onRetryFolder={(folderId) => triggerLoad(folderId, true)}
                  searchActive={Boolean(searchQuery.trim())}
                />
              </div>
            </div>

            {/* Right: Content Details */}
            <div className="flex-1 overflow-y-auto">
              <div className="p-6">
                {selectedContentItem ? (
                  isFetchingSelectedChapter && selectedContentItem.type === "chapter" ? (
                    <Loader2 className="w-5 h-5 animate-spin text-[hsl(var(--accent-primary))]" />
                  ) : <ContentDetail item={selectedContentItem} />
                ) : (
                  <EmptyState material={material} />
                )}
              </div>
            </div>
          </>
        )}
      </div>
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
          <FolderOpen className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
        ) : (
          <Folder className="w-4 h-4 text-[hsl(var(--text-tertiary))]" />
        );
      case "chapter":
        return <BookOpen className="w-4 h-4 text-blue-500" />;
      case "character":
        return <User className="w-4 h-4 text-purple-500" />;
      case "story":
        return <Sparkles className="w-4 h-4 text-amber-500" />;
      case "worldview":
        return <Globe className="w-4 h-4 text-green-500" />;
      case "cheat":
        return <Zap className="w-4 h-4 text-red-500" />;
      case "plot":
        return <File className="w-4 h-4 text-cyan-500" />;
      case "storyline":
        return <GitBranch className="w-4 h-4 text-indigo-500" />;
      case "relationship":
        return <Users className="w-4 h-4 text-pink-500" />;
      case "goldenfinger":
        return <Zap className="w-4 h-4 text-yellow-500" />;
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
              className={`w-full flex items-center gap-2 px-2 py-2.5 rounded-lg text-sm transition-colors ${
                isDisabled
                  ? "cursor-not-allowed text-[hsl(var(--text-tertiary))]"
                  : isSelected
                  ? "bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]"
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
              <div className="ml-8 py-1 text-xs text-[hsl(var(--error))]">
                <span>{t("materials:detail.folderLoadError", { defaultValue: "加载失败。" })}</span>{" "}
                <button className="underline" onClick={() => onRetryFolder(item.id)}>
                  {t("common:retry", { defaultValue: "重试" })}
                </button>
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

// Markdown 内容渲染组件
function MarkdownContent({ content, className = "" }: { content: string; className?: string }) {
  return (
    <div className={`prose prose-sm dark:prose-invert max-w-none
        prose-headings:text-[hsl(var(--text-primary))]
        prose-p:text-[hsl(var(--text-primary))]
        prose-strong:text-[hsl(var(--text-primary))]
        prose-ul:text-[hsl(var(--text-primary))]
        prose-ol:text-[hsl(var(--text-primary))]
        prose-li:text-[hsl(var(--text-primary))]
        prose-a:text-[hsl(var(--accent-primary))]
        ${className}`}>
      <LazyMarkdown>{content}</LazyMarkdown>
    </div>
  );
}

// ContentDetail Component
interface ContentDetailProps {
  item: TreeItem;
}

function ContentDetail({ item }: ContentDetailProps) {
  const { t } = useTranslation(["materials"]);

  if (item.type === "chapter") {
    const chapter = item.data as MaterialChapter;
    return (
      <div className="max-w-4xl">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <BookOpen className="w-5 h-5 text-blue-500" />
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))]">
              {chapter.title}
            </h2>
          </div>
          <div className="flex items-center gap-4 text-sm text-[hsl(var(--text-secondary))]">
            <span>{t("materials:detail.chapterNumber")}: {chapter.chapter_number}</span>
            <span>{t("materials:detail.wordCount")}: {chapter.word_count?.toLocaleString()}</span>
          </div>
        </div>

        {chapter.summary && (
          <div className="mb-6 p-4 rounded-lg bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))]">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.summary")}
            </h3>
            <MarkdownContent content={chapter.summary} />
          </div>
        )}

        {chapter.content && (
          <MarkdownContent content={chapter.content} />
        )}
      </div>
    );
  }

  if (item.type === "character") {
    const character = item.data as MaterialCharacter;
    const firstAppearanceChapter = character.first_appearance_chapter;
    return (
      <div className="max-w-3xl">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <User className="w-5 h-5 text-purple-500" />
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))]">
              {character.name}
            </h2>
          </div>
          {character.aliases && character.aliases.length > 0 && (
            <div className="flex items-center gap-2 text-sm text-[hsl(var(--text-secondary))]">
              <span>{t("materials:detail.aliases")}:</span>
              <span>{character.aliases.join(i18n.language === 'zh' ? '、' : ', ')}</span>
            </div>
          )}
        </div>

        {character.description && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.description")}
            </h3>
            <MarkdownContent content={character.description} />
          </div>
        )}

        {firstAppearanceChapter != null && (
          <div className="text-sm text-[hsl(var(--text-secondary))]">
            {t("materials:detail.firstAppearance")}: {t("materials:detail.chapter", { number: firstAppearanceChapter })}
          </div>
        )}
      </div>
    );
  }

  if (item.type === "story") {
    const story = item.data as MaterialStory;

    const parseThemes = (themes: string | null | undefined): string[] => {
      if (!themes) return [];
      if (typeof themes === 'string') {
        try {
          const parsed = JSON.parse(themes);
          return Array.isArray(parsed) ? parsed : [themes];
        } catch {
          return themes.split(',').map(t => t.trim()).filter(Boolean);
        }
      }
      return [];
    };

    return (
      <div className="max-w-3xl">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <Sparkles className="w-5 h-5 text-amber-500" />
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))]">
              {story.title}
            </h2>
          </div>
          {story.story_type && (
            <span className="inline-block px-3 py-1 rounded-full bg-amber-100 text-amber-700 text-xs font-medium">
              {materialEnumLabel(t, 'storyType', story.story_type)}
            </span>
          )}
        </div>

        <div className="mb-6">
          <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
            {t("materials:detail.description")}
          </h3>
          <MarkdownContent content={story.synopsis} />
        </div>

        {story.core_objective && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.coreObjective")}
            </h3>
            <MarkdownContent content={story.core_objective} />
          </div>
        )}

        {story.core_conflict && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.coreConflict")}
            </h3>
            <MarkdownContent content={story.core_conflict} />
          </div>
        )}

        {story.themes && parseThemes(story.themes).length > 0 && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.themes")}
            </h3>
            <div className="flex flex-wrap gap-2">
              {parseThemes(story.themes).map((theme, index) => (
                <span
                  key={index}
                  className="px-3 py-1 rounded-full bg-amber-100 text-amber-700 text-xs font-medium"
                >
                  {theme}
                </span>
              ))}
            </div>
          </div>
        )}

        {story.chapter_range && (
          <div>
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.relatedChapters")}
            </h3>
            <div className="flex flex-wrap gap-2">
              <span
                className="px-3 py-1 rounded-lg bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] text-sm text-[hsl(var(--text-primary))]"
              >
                {t("materials:detail.chapterRange", { range: story.chapter_range })}
              </span>
            </div>
          </div>
        )}
      </div>
    );
  }

  if (item.type === "plot") {
    const plot = item.data as MaterialPlot;
    return (
      <div className="max-w-3xl">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <File className="w-5 h-5 text-cyan-500" />
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))]">
              {t("materials:detail.plotTitle")}
            </h2>
          </div>
          <span className="inline-block px-3 py-1 rounded-full bg-cyan-100 text-cyan-700 text-xs font-medium">
            {materialEnumLabel(t, 'plotType', plot.plot_type)}
          </span>
        </div>

        <div className="mb-6">
          <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
            {t("materials:detail.description")}
          </h3>
          <MarkdownContent content={plot.description} />
        </div>

        {plot.characters && plot.characters.length > 0 && (
          <div>
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.involvedCharacters")}
            </h3>
            <div className="flex flex-wrap gap-2">
              {plot.characters.map((char: string, index: number) => (
                <span
                  key={index}
                  className="px-3 py-1 rounded-full bg-purple-100 text-purple-700 text-xs font-medium"
                >
                  {char}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (item.type === "storyline") {
    const storyline = item.data as MaterialStoryLine;
    return (
      <div className="max-w-3xl">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <GitBranch className="w-5 h-5 text-indigo-500" />
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))]">
              {storyline.title}
            </h2>
          </div>
        </div>

        {storyline.description && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.description")}
            </h3>
            <MarkdownContent content={storyline.description} />
          </div>
        )}

        {storyline.main_characters && storyline.main_characters.length > 0 && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.mainCharacters")}
            </h3>
            <div className="flex flex-wrap gap-2">
              {storyline.main_characters.map((char: string, index: number) => (
                <span
                  key={index}
                  className="px-3 py-1 rounded-full bg-purple-100 text-purple-700 text-xs font-medium"
                >
                  {char}
                </span>
              ))}
            </div>
          </div>
        )}

        {storyline.themes && storyline.themes.length > 0 && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.themes")}
            </h3>
            <div className="flex flex-wrap gap-2">
              {storyline.themes.map((theme: string, index: number) => (
                <span
                  key={index}
                  className="px-3 py-1 rounded-full bg-indigo-100 text-indigo-700 text-xs font-medium"
                >
                  {theme}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="text-sm text-[hsl(var(--text-secondary))]">
          {t("materials:detail.storiesCount")}: {storyline.stories_count}
        </div>
      </div>
    );
  }

  if (item.type === "relationship") {
    const relationship = item.data as MaterialCharacterRelationship;
    return (
      <div className="max-w-3xl">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <Users className="w-5 h-5 text-pink-500" />
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))]">
              {relationship.character_a_name} - {relationship.character_b_name}
            </h2>
          </div>
          <span className="inline-block px-3 py-1 rounded-full bg-pink-100 text-pink-700 text-xs font-medium">
            {materialEnumLabel(t, 'relationshipType', relationship.relationship_type)}
          </span>
        </div>

        {relationship.sentiment && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.sentiment")}
            </h3>
            <MarkdownContent content={relationship.sentiment} />
          </div>
        )}

        {relationship.description && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.description")}
            </h3>
            <MarkdownContent content={relationship.description} />
          </div>
        )}
      </div>
    );
  }

  if (item.type === "goldenfinger") {
    const goldenfinger = item.data as MaterialGoldenFinger;
    return (
      <div className="max-w-3xl">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <Zap className="w-5 h-5 text-yellow-500" />
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))]">
              {goldenfinger.name}
            </h2>
          </div>
          <span className="inline-block px-3 py-1 rounded-full bg-[hsl(var(--warning)/0.2)] text-[hsl(var(--warning))] text-xs font-medium">
            {materialEnumLabel(t, 'goldenFingerType', goldenfinger.type)}
          </span>
        </div>

        {goldenfinger.description && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.description")}
            </h3>
            <MarkdownContent content={goldenfinger.description} />
          </div>
        )}

        {goldenfinger.evolution_history && goldenfinger.evolution_history.length > 0 && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.evolutionHistory")}
            </h3>
            <div className="space-y-3">
              {goldenfinger.evolution_history.map((item, index) => (
                <div
                  key={index}
                  className="p-4 rounded-lg bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))]"
                >
                  {item.stage && (
                    <h4 className="font-semibold text-[hsl(var(--text-primary))] mb-2">
                      {item.stage}
                    </h4>
                  )}
                  {item.description && (
                    <div className="mb-2">
                      <MarkdownContent content={item.description} className="text-sm" />
                    </div>
                  )}
                  <div className="flex flex-wrap gap-3 text-xs text-[hsl(var(--text-tertiary))]">
                    {item.chapter && (
                      <span>
                        <span className="font-medium">{t("materials:detail.chapterLabel")}:</span> {item.chapter}
                      </span>
                    )}
                    {item.timestamp && (
                      <span>
                        <span className="font-medium">{t("materials:detail.timeLabel")}:</span> {item.timestamp}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (item.type === "worldview") {
    const worldview = item.data as MaterialWorldView;
    return (
      <div className="max-w-3xl">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <Globe className="w-5 h-5 text-green-500" />
            <h2 className="text-2xl font-bold text-[hsl(var(--text-primary))]">
              {t("materials:detail.worldview")}
            </h2>
          </div>
        </div>

        {worldview.power_system && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.powerSystem")}
            </h3>
            <MarkdownContent content={worldview.power_system} />
          </div>
        )}

        {worldview.world_structure && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.worldStructure")}
            </h3>
            <MarkdownContent content={worldview.world_structure} />
          </div>
        )}

        {worldview.key_factions && worldview.key_factions.length > 0 && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.keyFactions")}
            </h3>
            <div className="space-y-3">
              {worldview.key_factions.map((faction, index) => (
                <div
                  key={index}
                  className="p-4 rounded-lg bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))]"
                >
                  <h4 className="font-semibold text-[hsl(var(--text-primary))] mb-2">
                    {faction.name}
                  </h4>
                  {faction.description && (
                    <div className="mb-2">
                      <MarkdownContent content={faction.description} className="text-sm" />
                    </div>
                  )}
                  <div className="flex flex-wrap gap-3 text-xs text-[hsl(var(--text-tertiary))]">
                    {faction.leader && (
                      <span>
                        <span className="font-medium">{t("materials:detail.leader")}:</span> {faction.leader}
                      </span>
                    )}
                    {faction.territory && (
                      <span>
                        <span className="font-medium">{t("materials:detail.territory")}:</span> {faction.territory}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {worldview.special_rules && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.specialRules")}
            </h3>
            <MarkdownContent content={worldview.special_rules} />
          </div>
        )}
      </div>
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
      <div className="w-16 h-16 rounded-2xl bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center mb-4">
        <BookOpen className="w-8 h-8 text-[hsl(var(--accent-primary))]" />
      </div>
      <h3 className="text-lg font-semibold text-[hsl(var(--text-primary))] mb-2">
        {t("materials:detail.emptyTitle")}
      </h3>
      <p className="text-sm text-[hsl(var(--text-secondary))] max-w-md">
        {t("materials:detail.emptyDescription")}
      </p>

      {isActiveMaterialStatus(material.status) && (
        <div className="mt-6 flex items-center gap-2 text-sm text-blue-600">
          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600" />
          <span>{t(`materials:status.${material.status}`)}</span>
        </div>
      )}
    </div>
  );
}

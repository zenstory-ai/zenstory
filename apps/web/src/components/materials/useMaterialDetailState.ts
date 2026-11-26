import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { materialsApi, type MaterialNovel } from "../../lib/materialsApi";
import { subscriptionApi, subscriptionQueryKeys } from "../../lib/subscriptionApi";
import { hasMaterialsLibraryAccess } from "../../lib/materialsAccess";
import { handleApiError } from "../../lib/errorHandler";
import { toast } from "../../lib/toast";
import { materialsConfig } from "../../config/materials";
import { useRefreshMaterialLibraryOnCompletion } from "../../hooks/useMaterialLibraryRefresh";
import {
  isActiveMaterialStatus,
  isStageEnabled,
  MATERIAL_ITEM_FOLDERS,
  parseThemes,
  readableText,
  shouldShowFolder,
  type MaterialTreeItem,
} from "./materialHelpers";

export interface FolderQueryMeta {
  isFetching: boolean;
  isSuccess: boolean;
  isError: boolean;
  refetch: () => Promise<unknown>;
}

export function useMaterialDetailState(novelId: string | undefined, isMobile: boolean) {
  const { t } = useTranslation(["materials", "common"]);
  const queryClient = useQueryClient();

  const [searchQuery, setSearchQuery] = useState("");
  const [selectedItem, setSelectedItem] = useState<MaterialTreeItem | null>(null);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [folderNavigationTarget, setFolderNavigationTarget] = useState<string | null>(null);
  // 移动端：是否显示内容详情（false = 显示文件树）
  const [showMobileContent, setShowMobileContent] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isExporting, setIsExporting] = useState(false);

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

  const handleExport = async () => {
    if (!novelId || !material) return;
    setIsExporting(true);
    try {
      await materialsApi.exportBreakdown(novelId, material.title);
      toast.success(
        t("materials:export.success", {
          defaultValue: "拆解结果已导出为 TXT 文件",
        }),
      );
    } catch (err) {
      toast.error(handleApiError(err));
    } finally {
      setIsExporting(false);
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
    relationships:
      (materialsConfig.relationshipsEnabled || Number(material?.relationships_count) > 0) &&
      shouldShowFolder(isStageEnabled(enabledStages, "relationships"), material?.relationships_count),
    goldenfingers: shouldShowFolder(isStageEnabled(enabledStages, "meta"), material?.golden_fingers_count),
    worldview: shouldShowFolder(isStageEnabled(enabledStages, "meta"), material?.has_world_view),
  };

  const folderCounts: Record<string, number | undefined> = {
    chapters: material?.chapters_count,
    characters: material?.characters_count,
    stories: material?.stories_count,
    plots: material?.plots_count,
    storylines: material?.story_lines_count,
    relationships: material?.relationships_count,
    goldenfingers: material?.golden_fingers_count,
    worldview: material?.has_world_view === undefined ? undefined : Number(material.has_world_view),
  };

  const shouldLoadFolder = (folderId: string) =>
    Boolean(
      novelId &&
        material &&
        folderVisibility[folderId] &&
        (expandedFolders.has(folderId) ||
          searchQuery.trim() ||
          (selectedItem && MATERIAL_ITEM_FOLDERS[selectedItem.type] === folderId)),
    );

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

  const charactersQuery = useQuery({
    queryKey: ["material-characters", novelId, materialVersion],
    queryFn: () => materialsApi.getCharacters(novelId!),
    enabled: shouldLoadFolder("characters"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  const storiesQuery = useQuery({
    queryKey: ["material-stories", novelId, materialVersion],
    queryFn: () => materialsApi.getStories(novelId!),
    enabled: shouldLoadFolder("stories"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  const plotsQuery = useQuery({
    queryKey: ["material-plots", novelId, materialVersion],
    queryFn: () => materialsApi.getPlots(novelId!),
    enabled: shouldLoadFolder("plots"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  const storylinesQuery = useQuery({
    queryKey: ["material-storylines", novelId, materialVersion],
    queryFn: () => materialsApi.getStoryLines(novelId!),
    enabled: shouldLoadFolder("storylines"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  const relationshipsQuery = useQuery({
    queryKey: ["material-relationships", novelId, materialVersion],
    queryFn: () => materialsApi.getRelationships(novelId!),
    enabled: shouldLoadFolder("relationships"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

  const goldenfingersQuery = useQuery({
    queryKey: ["material-goldenfingers", novelId, materialVersion],
    queryFn: () => materialsApi.getGoldenFingers(novelId!),
    enabled: shouldLoadFolder("goldenfingers"),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });

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

  const folderQueries: Record<string, FolderQueryMeta> = {
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
  const loadedStates = Object.fromEntries(
    Object.entries(folderQueries).map(([id, query]) => [id, query.isSuccess]),
  );

  // Build tree structure - folders always visible, children lazy loaded
  const treeData: MaterialTreeItem[] = [];
  treeData.push({
    id: "chapters",
    type: "folder",
    title: t("materials:detail.chapters"),
    count: folderCounts.chapters,
    children: chapters.map((chapter) => ({
      id: chapter.id,
      type: "chapter",
      title:
        chapter.title ||
        t("materials:detail.chapter", {
          number: Number(chapter.metadata?.chapter_number ?? 0),
        }),
      data: chapter,
    })),
  });

  if (folderVisibility.characters) {
    treeData.push({
      id: "characters",
      type: "folder",
      title: t("materials:detail.characters"),
      count: folderCounts.characters,
      children: characters.map((character) => ({
        id: character.id,
        type: "character",
        title: character.name,
        data: character,
        searchText: readableText([character.name, character.aliases ?? [], character.description]),
      })),
    });
  }

  if (folderVisibility.stories) {
    treeData.push({
      id: "stories",
      type: "folder",
      title: t("materials:detail.stories"),
      count: folderCounts.stories,
      children: stories.map((story) => ({
        id: story.id,
        type: "story",
        title: story.title,
        data: story,
        searchText: readableText([
          story.title,
          story.synopsis,
          story.core_objective,
          story.core_conflict,
          parseThemes(story.themes),
        ]),
      })),
    });
  }

  if (folderVisibility.plots) {
    treeData.push({
      id: "plots",
      type: "folder",
      title: t("materials:detail.plots"),
      count: folderCounts.plots,
      children: plots.map((plot) => ({
        id: String(plot.id),
        type: "plot",
        title: plot.description.substring(0, 50) + (plot.description.length > 50 ? "…" : ""),
        data: plot,
        searchText: readableText([plot.description, plot.characters ?? []]),
      })),
    });
  }

  if (folderVisibility.storylines) {
    treeData.push({
      id: "storylines",
      type: "folder",
      title: t("materials:detail.storylines"),
      count: folderCounts.storylines,
      children: storylines.map((storyline) => ({
        id: String(storyline.id),
        type: "storyline",
        title: storyline.title,
        data: storyline,
        searchText: readableText([
          storyline.title,
          storyline.description,
          storyline.main_characters ?? [],
          storyline.themes ?? [],
        ]),
      })),
    });
  }

  if (folderVisibility.relationships) {
    treeData.push({
      id: "relationships",
      type: "folder",
      title: t("materials:detail.relationships"),
      count: folderCounts.relationships,
      children: relationships.map((rel) => ({
        id: String(rel.id),
        type: "relationship",
        title: `${rel.character_a_name} - ${rel.character_b_name}`,
        data: rel,
        searchText: readableText([
          rel.character_a_name,
          rel.character_b_name,
          rel.description,
          rel.relationship_type,
          rel.sentiment,
        ]),
      })),
    });
  }

  if (folderVisibility.goldenfingers) {
    treeData.push({
      id: "goldenfingers",
      type: "folder",
      title: t("materials:detail.goldenfingers"),
      count: folderCounts.goldenfingers,
      children: goldenFingers.map((gf) => ({
        id: String(gf.id),
        type: "goldenfinger",
        title: gf.name,
        data: gf,
        searchText: readableText([
          gf.name,
          gf.type,
          gf.description,
          ...(gf.evolution_history ?? []).flatMap((entry) => [entry.stage, entry.description, entry.timestamp]),
        ]),
      })),
    });
  }

  if (folderVisibility.worldview) {
    treeData.push({
      id: "worldview",
      type: "folder",
      title: t("materials:detail.worldview"),
      count: folderCounts.worldview,
      children: worldview
        ? [
            {
              id: String(worldview.id),
              type: "worldview",
              title: t("materials:detail.worldviewItem"),
              data: worldview,
              searchText: readableText([
                worldview.power_system,
                worldview.world_structure,
                worldview.special_rules,
                ...(worldview.key_factions ?? []).flatMap((faction) => [
                  faction.name,
                  faction.description,
                  faction.leader,
                  faction.territory,
                ]),
              ]),
            },
          ]
        : [],
    });
  }

  const filterTree = (items: MaterialTreeItem[], query: string): MaterialTreeItem[] => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    if (!normalizedQuery) return items;

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

        if ((item.searchText ?? item.title).toLocaleLowerCase().includes(normalizedQuery)) {
          return item;
        }

        return null;
      })
      .filter((item): item is MaterialTreeItem => item !== null);
  };

  const filteredTree = filterTree(treeData, searchQuery);
  const searchComplete = treeData.every((folder) => folderQueries[folder.id]?.isSuccess);

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

  const handleItemClick = (item: MaterialTreeItem) => {
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

  const handleOverviewClick = () => {
    setSelectedItem(null);
    if (isMobile) setShowMobileContent(true);
  };

  const handleOverviewCategoryClick = (folderId: string) => {
    setSearchQuery("");
    setExpandedFolders((prev) => new Set(prev).add(folderId));
    if (isMobile) setShowMobileContent(false);
    setFolderNavigationTarget(folderId);
  };

  useEffect(() => {
    if (!folderNavigationTarget) return;
    const folderButton = document.getElementById(`material-folder-${folderNavigationTarget}`);
    if (!folderButton) return;
    folderButton.scrollIntoView?.({ block: "nearest" });
    folderButton.focus({ preventScroll: true });
    setFolderNavigationTarget(null);
  }, [folderNavigationTarget, searchQuery, showMobileContent, expandedFolders, filteredTree]);

  // Selection keeps its identity; refreshed directory data owns the displayed reference.
  const currentSelectedItem =
    selectedItem &&
    (treeData
      .flatMap((folder) => folder.children ?? [])
      .find((item) => item.type === selectedItem.type && item.id === selectedItem.id) ??
      selectedItem);
  const selectedContentItem =
    currentSelectedItem?.type === "chapter" && selectedChapter
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

  const handleRetryFolder = (folderId: string) => {
    void folderQueries[folderId]?.refetch();
  };

  return {
    material,
    materialLoading,
    materialError,
    refetchMaterial,
    canRetry,
    isRetrying,
    isExporting,
    handleRetry,
    handleExport,
    searchQuery,
    setSearchQuery,
    handleSearchChange,
    selectedItem,
    selectedContentItem,
    selectedChapter,
    isFetchingSelectedChapter,
    selectedChapterError: selectedChapterQuery.isError,
    refetchSelectedChapter: selectedChapterQuery.refetch,
    expandedFolders,
    showMobileContent,
    handleMobileBack,
    treeData,
    filteredTree,
    searchComplete,
    loadingStates,
    loadedStates,
    folderErrors,
    handleRetryFolder,
    handleItemClick,
    handleOverviewClick,
    handleOverviewCategoryClick,
  };
}

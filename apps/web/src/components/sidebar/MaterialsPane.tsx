import React, { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useProject } from "../../contexts/ProjectContext";
import { useMaterialAttachment } from "../../contexts/MaterialAttachmentContext";
import { useMaterialLibraryContext } from "../../contexts/MaterialLibraryContext";
import { materialsConfig } from "../../config/materials";
import { materialsApi } from "../../lib/materialsApi";
import { toast } from "../../lib/toast";
import type { MaterialEntityType, MaterialSearchResult } from "../../lib/materialsApi";
import { logger } from "../../lib/logger";
import { MaterialsUpgradeNotice } from "../subscription/MaterialsUpgradePrompt";
import {
  ChevronDown,
  ChevronRight,
  BookOpen,
  Users,
  Globe,
  Sparkles,
  GitBranch,
  Heart,
  Plus,
  MessageSquarePlus,
  Check,
  X,
} from "lucide-react";

// Entity type icons mapping
const ENTITY_TYPE_ICONS: Record<MaterialEntityType, React.ReactNode> = {
  characters: <Users size={12} />,
  worldview: <Globe size={12} />,
  goldenfingers: <Sparkles size={12} />,
  storylines: <GitBranch size={12} />,
  stories: <BookOpen size={12} />,
  relationships: <Heart size={12} />,
};

type MaterialCountKey =
  | "characters"
  | "worldview"
  | "golden_fingers"
  | "storylines"
  | "stories"
  | "relationships";

/**
 * MaterialsPane - Material library browser component
 *
 * Sidebar pane for material libraries.
 * Displays AI-extracted material libraries with search, batch selection, and import.
 */
export const MaterialsPane: React.FC = () => {
  const { t } = useTranslation(['editor', 'common', 'materials']);
  const { currentProjectId, triggerFileTreeRefresh } = useProject();
  const { addMaterial } = useMaterialAttachment();
  const materialLib = useMaterialLibraryContext();
  const isLibraryLoading = materialLib.isLoading || (materialLib.isFetching && materialLib.libraries.length === 0);
  const { refreshIfStale } = materialLib;

  // Decompositions may have finished since the summary was cached.
  useEffect(() => {
    refreshIfStale();
  }, [refreshIfStale]);

  // Entity lists state
  const [entityLists, setEntityLists] = useState<Record<string, { id: number; name: string }[]>>({});
  const [entityListLoading, setEntityListLoading] = useState<Record<string, boolean>>({});

  // Batch selection state
  const [batchMode, setBatchMode] = useState(false);
  const [selectedItems, setSelectedItems] = useState<Set<string>>(new Set()); // key: "novelId:entityType:entityId"
  const [isBatchImporting, setIsBatchImporting] = useState(false);

  const importOwnerRef = useRef<{ projectId: string | null } | null>(null);
  useEffect(() => {
    const owner = { projectId: currentProjectId };
    importOwnerRef.current = owner;
    // A departed project's pending import must not lock the next project.
    setIsBatchImporting(false);
    return () => {
      if (importOwnerRef.current === owner) importOwnerRef.current = null;
    };
  }, [currentProjectId]);

  // Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<MaterialSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState(false);

  // Track loading state with ref to avoid stale closure
  const entityListLoadingRef = useRef<Record<string, boolean>>({});

  // Load entity list for a specific novel + entity type
  const loadEntityList = useCallback(async (novelId: number, entityType: MaterialEntityType) => {
    const key = `${novelId}:${entityType}`;

    // Check ref to avoid duplicate requests
    if (entityListLoadingRef.current[key]) return;

    // Also check if already loaded
    if (entityLists[key]) return;

    entityListLoadingRef.current[key] = true;
    setEntityListLoading(prev => ({ ...prev, [key]: true }));

    try {
      let items: { id: number; name: string }[] = [];
      const novel = materialLib.libraries.find(l => l.id === novelId);
      if (!novel) return;

      // Use existing API endpoints to get entity lists
      if (entityType === 'characters') {
        const data = await materialsApi.getCharacters(String(novelId));
        items = data.map((c) => ({ id: Number(c.id), name: c.name }));
      } else if (entityType === 'worldview') {
        const data = await materialsApi.getWorldView(String(novelId));
        if (data) {
          const displayName = (data.world_structure && data.world_structure.trim())
            ? data.world_structure.substring(0, 30)
            : t('materials:detail.worldview');
          items = [{ id: data.id, name: displayName }];
        }
      } else if (entityType === 'goldenfingers') {
        const data = await materialsApi.getGoldenFingers(String(novelId));
        items = data.map((g) => ({ id: g.id, name: g.name }));
      } else if (entityType === 'storylines') {
        const data = await materialsApi.getStoryLines(String(novelId));
        items = data.map((s) => ({ id: s.id, name: s.title }));
      } else if (entityType === 'stories') {
        const data = await materialsApi.getStories(String(novelId));
        items = data.map((s) => ({ id: Number(s.id), name: s.title }));
      } else if (entityType === 'relationships') {
        const data = await materialsApi.getRelationships(String(novelId));
        items = data.map((r) => ({ id: r.id, name: `${r.character_a_name} ↔ ${r.character_b_name}` }));
      }

      setEntityLists(prev => ({ ...prev, [key]: items }));
    } catch (err) {
      logger.error('Failed to load entity list:', err);
      toast.error(t('materials:toast.loadListFailed'));
    } finally {
      entityListLoadingRef.current[key] = false;
      setEntityListLoading(prev => ({ ...prev, [key]: false }));
    }
  }, [entityLists, materialLib.libraries, t]);

  // Handle entity type toggle with lazy loading
  const handleEntityTypeToggle = useCallback((novelId: number, entityType: MaterialEntityType) => {
    materialLib.toggleEntityType(novelId, entityType);
    loadEntityList(novelId, entityType);
  }, [materialLib, loadEntityList]);

  // Handle entity item click - load preview
  const handleEntityClick = useCallback((novelId: number, entityType: MaterialEntityType, entityId: number) => {
    materialLib.loadPreview(novelId, entityType, entityId);
  }, [materialLib]);

  // Quick import - use default parameters, no dialog
  const handleQuickImport = useCallback(async (novelId: number, entityType: MaterialEntityType, entityId: number) => {
    const owner = importOwnerRef.current;
    if (!currentProjectId || !owner || owner.projectId !== currentProjectId) return;
    try {
      await materialsApi.importToProject({
        project_id: currentProjectId,
        novel_id: novelId,
        entity_type: entityType,
        entity_id: entityId,
      });
      if (importOwnerRef.current === owner) triggerFileTreeRefresh();
      toast.success(t('materials:toast.importSuccess'));
    } catch (err) {
      logger.error('Failed to quick import material:', err);
      toast.error(t('materials:toast.importFailed'));
    }
  }, [currentProjectId, triggerFileTreeRefresh, t]);

  // Attach library material to chat
  const handleAttachToChat = useCallback((novelId: number, entityType: MaterialEntityType, entityId: number, itemName: string) => {
    // Use a virtual ID for library materials
    const virtualId = `lib_${novelId}_${entityType}_${entityId}`;
    addMaterial(virtualId, itemName, {
      novelId,
      entityType,
      entityId,
    });
  }, [addMaterial]);

  // Search timeout and request ordering refs
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchRequestSeqRef = useRef(0);

  // Handle search with debouncing
  const handleSearch = useCallback((query: string) => {
    setSearchQuery(query);
    const requestSeq = searchRequestSeqRef.current + 1;
    searchRequestSeqRef.current = requestSeq;

    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }
    if (!query.trim()) {
      setSearchResults([]);
      setSearchError(false);
      setIsSearching(false);
      return;
    }
    searchTimeoutRef.current = setTimeout(async () => {
      setIsSearching(true);
      setSearchError(false);
      try {
        const results = await materialsApi.searchMaterials(query);
        if (searchRequestSeqRef.current === requestSeq) {
          setSearchResults(results);
        }
      } catch (err) {
        logger.error('Failed to search materials:', err);
        if (searchRequestSeqRef.current === requestSeq) {
          setSearchResults([]);
          setSearchError(true);
        }
      } finally {
        if (searchRequestSeqRef.current === requestSeq) {
          setIsSearching(false);
        }
      }
    }, 300);
  }, []);

  // Cleanup search timeout on unmount
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
  }, []);

  // Batch mode handlers
  const toggleBatchMode = useCallback(() => {
    setBatchMode(prev => !prev);
    setSelectedItems(new Set());
  }, []);

  const toggleItemSelection = useCallback((novelId: number, entityType: MaterialEntityType, entityId: number) => {
    const key = `${novelId}:${entityType}:${entityId}`;
    setSelectedItems(prev => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const handleBatchImport = useCallback(async () => {
    const owner = importOwnerRef.current;
    if (!currentProjectId || selectedItems.size === 0 || !owner || owner.projectId !== currentProjectId) return;
    setIsBatchImporting(true);
    try {
      const items = Array.from(selectedItems).map(key => {
        const [novelId, entityType, entityId] = key.split(':');
        return {
          novel_id: Number(novelId),
          entity_type: entityType as MaterialEntityType,
          entity_id: Number(entityId),
        };
      });
      const result = await materialsApi.batchImport(currentProjectId, items);
      if (result.results.length > 0 && importOwnerRef.current === owner) triggerFileTreeRefresh();
      if (result.failed_count > 0) {
        toast.error(t('materials:toast.batchImportPartialFailed', {
          successCount: result.results.length,
          failedCount: result.failed_count,
        }));
        return;
      }

      toast.success(t('materials:toast.batchImportSuccess', { count: items.length }));
      if (importOwnerRef.current === owner) {
        setBatchMode(false);
        setSelectedItems(new Set());
      }
    } catch (err) {
      logger.error('Failed to batch import:', err);
      toast.error(t('materials:toast.batchImportFailed'));
    } finally {
      if (importOwnerRef.current === owner) setIsBatchImporting(false);
    }
  }, [currentProjectId, selectedItems, triggerFileTreeRefresh, t]);

  // Entity type labels
  const entityTypeLabels: Record<MaterialEntityType, string> = {
    characters: t('editor:fileTree.referenceCharacters'),
    worldview: t('editor:fileTree.referenceWorldview'),
    goldenfingers: t('editor:fileTree.referenceGoldenFingers'),
    storylines: t('editor:fileTree.referenceStorylines'),
    stories: t('editor:fileTree.referenceStories'),
    relationships: t('editor:fileTree.referenceRelationships'),
  };

  const entityTypes: { type: MaterialEntityType; countKey: MaterialCountKey }[] = [
    { type: 'characters', countKey: 'characters' },
    { type: 'worldview', countKey: 'worldview' },
    { type: 'goldenfingers', countKey: 'golden_fingers' },
    { type: 'storylines', countKey: 'storylines' },
    { type: 'stories', countKey: 'stories' },
    ...(materialsConfig.relationshipsEnabled || materialLib.libraries.some((library) => Number(library.counts.relationships) > 0)
      ? [{ type: 'relationships' as const, countKey: 'relationships' as const }]
      : []),
  ];

  return (
    <div className="w-full h-full text-sm overflow-auto p-2">
      {/* Header */}
      <div className="flex items-center justify-between mb-2 px-2">
        <div className="flex items-center gap-2">
          <BookOpen size={14} className="text-[hsl(var(--text-secondary))]" />
          <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
            {t('editor:fileTree.referenceLibrary')}
          </span>
          <span className="text-[10px] text-[hsl(var(--text-secondary))]">
            ({t('editor:fileTree.aiExtracted')})
          </span>
        </div>
        <button
          onClick={toggleBatchMode}
          className={`p-1 text-xs ${batchMode ? 'text-[hsl(var(--accent-primary))]' : 'text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))]'} transition-all`}
          title={batchMode ? t('common:cancel') : t('editor:fileTree.batchSelect')}
        >
          {batchMode ? <X size={14} /> : <Check size={14} />}
        </button>
      </div>

      {/* Search input - only show when there are libraries */}
      {materialLib.libraries.length > 0 && (
        <div className="px-2 py-1 mb-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder={t('editor:fileTree.searchMaterials')}
            className="w-full px-2 py-1 text-xs rounded border border-[hsl(var(--border-primary))] bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))]"
          />
        </div>
      )}

      {/* Search results */}
      {searchQuery.trim() && (
        <div className="mb-2">
          {isSearching ? (
            <div className="px-2 py-2 text-[hsl(var(--text-secondary))] text-xs italic">
              {t('common:loading')}
            </div>
          ) : searchError ? (
            <div className="px-2 py-2 text-xs text-[hsl(var(--error))]">
              <p>{t('materials:searchLoadFailed', { defaultValue: '素材搜索失败，请重试。' })}</p>
              <button className="mt-1 underline" onClick={() => handleSearch(searchQuery)}>
                {t('common:retry', { defaultValue: '重试' })}
              </button>
            </div>
          ) : searchResults.length === 0 ? (
            <div className="px-2 py-2 text-[hsl(var(--text-secondary))] text-xs italic">
              {t('editor:fileTree.noSearchResults')}
            </div>
          ) : (
            searchResults.map((result) => (
              <div
                key={`${result.novel_id}:${result.entity_type}:${result.entity_id}`}
                className="flex items-center gap-2 px-2 py-1 rounded cursor-pointer group transition-colors text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
                onClick={() => handleEntityClick(result.novel_id, result.entity_type, result.entity_id)}
              >
                <span className="w-3" />
                <div className="flex-1 min-w-0">
                  <div className="text-xs truncate">{result.name}</div>
                  <div className="text-[10px] text-[hsl(var(--text-secondary))] truncate">{result.novel_title}</div>
                </div>
                <div className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 transition-all">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleAttachToChat(result.novel_id, result.entity_type, result.entity_id, result.name);
                    }}
                    className="p-0.5 text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] transition-colors"
                    title={t('editor:fileTree.attachToChat')}
                  >
                    <MessageSquarePlus size={12} />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleQuickImport(result.novel_id, result.entity_type, result.entity_id);
                    }}
                    className="p-0.5 text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] transition-colors"
                    title={t('editor:fileTree.addToProject')}
                  >
                    <Plus size={12} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Normal library tree (only show when not searching) */}
      {!searchQuery.trim() && (
        isLibraryLoading ? (
          <div className="px-2 py-2 text-[hsl(var(--text-secondary))] text-xs italic">
            {t('common:loading')}
          </div>
        ) : materialLib.accessDenied ? (
          <MaterialsUpgradeNotice source="editor_materials_pane" className="px-3 py-3" />
        ) : materialLib.error ? (
          <div className="px-3 py-3 text-xs text-[hsl(var(--error))]">
            <p>{t('materials:libraryLoadFailed', { defaultValue: '素材库加载失败，请重试。' })}</p>
            <button className="mt-1 underline" onClick={() => void materialLib.refetch()}>
              {t('common:retry', { defaultValue: '重试' })}
            </button>
          </div>
        ) : materialLib.libraries.length === 0 ? (
          <div className="px-3 py-3 text-[hsl(var(--text-secondary))] text-xs">
            <p className="italic mb-1">{t('editor:fileTree.referenceLibraryEmpty')}</p>
            <p className="text-[10px] opacity-70">{t('editor:fileTree.referenceLibraryEmptyHint')}</p>
          </div>
        ) : (
          materialLib.libraries.map((lib) => {
            const isNovelExpanded = materialLib.expandedNovels.has(lib.id);

            return (
              <div key={lib.id} className="mb-1">
                {/* Novel header */}
                <div
                  className="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer group transition-colors text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
                  onClick={() => materialLib.toggleNovel(lib.id)}
                >
                  {isNovelExpanded ? (
                    <ChevronDown size={14} className="text-[hsl(var(--text-secondary))] shrink-0" />
                  ) : (
                    <ChevronRight size={14} className="text-[hsl(var(--text-secondary))] shrink-0" />
                  )}
                  <span className="text-[hsl(var(--text-secondary))]">
                    <BookOpen size={14} />
                  </span>
                  <span className="flex-1 truncate text-sm">{lib.title}</span>
                </div>

                {/* Entity type categories */}
                {isNovelExpanded && (
                  <div className="mt-0.5">
                    {entityTypes
                      .filter(({ countKey }) => (lib.counts[countKey] || 0) > 0)
                      .map(({ type, countKey }) => {
                        const typeKey = `${lib.id}:${type}`;
                        const isTypeExpanded = materialLib.expandedTypes.get(typeKey) || false;
                        const items = entityLists[typeKey] || [];
                        const isLoadingItems = entityListLoading[typeKey] || false;

                        return (
                          <div key={type}>
                            {/* Entity type header */}
                            <div
                              className="flex items-center gap-2 px-2 py-1 rounded cursor-pointer group transition-colors text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
                              style={{ marginLeft: 12 }}
                              onClick={() => handleEntityTypeToggle(lib.id, type)}
                            >
                              {isTypeExpanded ? (
                                <ChevronDown size={12} className="text-[hsl(var(--text-secondary))] shrink-0" />
                              ) : (
                                <ChevronRight size={12} className="text-[hsl(var(--text-secondary))] shrink-0" />
                              )}
                              <span className="text-[hsl(var(--text-secondary))] shrink-0">
                                {ENTITY_TYPE_ICONS[type]}
                              </span>
                              <span className="flex-1 truncate text-xs">{entityTypeLabels[type]}</span>
                              <span className="text-[hsl(var(--text-secondary))] text-xs">
                                {lib.counts[countKey] || 0}
                              </span>
                            </div>

                            {/* Entity items */}
                            {isTypeExpanded && (
                              <div className="mt-0.5">
                                {isLoadingItems ? (
                                  <div className="px-2 py-1 text-[hsl(var(--text-secondary))] text-xs italic" style={{ marginLeft: 24 }}>
                                    {t('common:loading')}
                                  </div>
                                ) : (
                                  items.map((item) => (
                                    <div
                                      key={item.id}
                                      className="flex items-center gap-2 px-2 py-1 rounded cursor-pointer group transition-colors text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
                                      style={{ marginLeft: 24 }}
                                      onClick={() => handleEntityClick(lib.id, type, item.id)}
                                    >
                                      {batchMode && (
                                        <input
                                          type="checkbox"
                                          checked={selectedItems.has(`${lib.id}:${type}:${item.id}`)}
                                          onChange={(e) => {
                                            e.stopPropagation();
                                            toggleItemSelection(lib.id, type, item.id);
                                          }}
                                          className="shrink-0"
                                        />
                                      )}
                                      {!batchMode && <span className="w-3" />}
                                      <span className="flex-1 truncate text-xs">{item.name}</span>
                                      {!batchMode && (
                                        <div className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 transition-all">
                                          <button
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              handleAttachToChat(lib.id, type, item.id, item.name);
                                            }}
                                            className="p-0.5 text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] transition-colors"
                                            title={t('editor:fileTree.attachToChat')}
                                          >
                                            <MessageSquarePlus size={12} />
                                          </button>
                                          <button
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              handleQuickImport(lib.id, type, item.id);
                                            }}
                                            className="p-0.5 text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] transition-colors"
                                            title={t('editor:fileTree.addToProject')}
                                          >
                                            <Plus size={12} />
                                          </button>
                                        </div>
                                      )}
                                    </div>
                                  ))
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                  </div>
                )}
              </div>
            );
          })
        )
      )}

      {/* Batch import action bar */}
      {batchMode && selectedItems.size > 0 && (
        <div className="px-2 py-2 border-t border-[hsl(var(--border-primary))] flex items-center justify-between mt-2">
          <span className="text-xs text-[hsl(var(--text-secondary))]">
            {t('editor:fileTree.batchSelected', { count: selectedItems.size })}
          </span>
          <button
            onClick={handleBatchImport}
            disabled={isBatchImporting}
            className="px-2 py-1 text-xs rounded bg-[hsl(var(--accent-primary))] text-white hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {isBatchImporting ? t('common:loading') : t('editor:fileTree.batchImport')}
          </button>
        </div>
      )}

    </div>
  );
};

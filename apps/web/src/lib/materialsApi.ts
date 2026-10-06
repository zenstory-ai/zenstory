/**
 * Material Library (素材库) API Client
 *
 * Handles novel file uploads, decomposition, and retrieval of structured content
 * including chapters, characters, and story elements.
 *
 * This module provides a typed API client for interacting with the backend
 * material decomposition service. It supports:
 * - Uploading novel files for AI-powered decomposition
 * - Polling decomposition status
 * - Retrieving extracted entities (characters, storylines, worldbuilding)
 * - Importing materials as project files
 */

import { api, ApiError, tryRefreshToken, getAccessToken, getApiBase, resolveOwnedAuthSession } from "./apiClient";
import { resolveApiErrorMessage } from "./errorHandler";

// ==================== Type Definitions ====================

/**
 * Represents a material library created from an uploaded novel file.
 */
export interface MaterialNovel {
  /** Unique identifier for the material library */
  id: string;
  /** ID of the user who owns this material */
  user_id: string;
  /** Display title of the novel */
  title: string;
  /** Original filename of the uploaded file */
  original_filename: string;
  /** File size in bytes */
  file_size: number;
  /** Processing status of the decomposition task */
  status: 'pending' | 'processing' | 'completed' | 'completed_with_errors' | 'failed';
  /** Error message if status is 'failed' */
  error_message?: string;
  /** Total number of chapters detected */
  total_chapters?: number;
  /** Number of chapters processed */
  chapters_count?: number;
  /** Total number of characters extracted */
  total_characters?: number;
  /**
   * Per-folder data counts (detail endpoint only). A folder whose stage is off
   * is only shown as "not enabled" when its count is 0.
   */
  characters_count?: number;
  plots_count?: number;
  stories_count?: number;
  story_lines_count?: number;
  relationships_count?: number;
  golden_fingers_count?: number;
  has_world_view?: boolean;
  /**
   * Decomposition stages that were effective for the latest job.
   * null/undefined for jobs created before the snapshot existed.
   */
  enabled_stages?: MaterialEnabledStages | null;
  /** UTC timestamp of creation */
  created_at: string;
  /** UTC timestamp of last update */
  updated_at: string;
}

/**
 * Effective decomposition stage map recorded when the ingestion job started
 * (backend: IngestionJob.stage_progress["enabled_stages"]).
 */
export interface MaterialEnabledStages {
  /** Per-chapter summaries */
  chapter_summaries: boolean;
  /** Per-chapter plot points (情节点) */
  plots: boolean;
  /** Character mentions + consolidated characters (角色) */
  characters: boolean;
  /** Golden fingers + worldview (金手指 / 世界观) */
  meta: boolean;
  /** Whole-novel synopsis */
  synopsis: boolean;
  /**
   * Story aggregation (剧情). Snapshots recorded before `storylines` existed
   * used this key for both 剧情 and 故事线.
   */
  stories: boolean;
  /** Storylines (故事线); absent in older snapshots */
  storylines?: boolean;
  /** Character relationships */
  relationships: boolean;
}

/**
 * Represents a chapter extracted from a novel.
 */
export interface MaterialChapter {
  /** Unique identifier for the chapter */
  id: string;
  /** ID of the parent material library */
  novel_id: string;
  /** Sequential chapter number */
  chapter_number: number;
  /** Chapter title */
  title: string;
  /** Full chapter content text */
  content: string;
  /** Word count of the chapter */
  word_count: number;
  /** AI-generated chapter summary */
  summary?: string;
  /** UTC timestamp of creation */
  created_at: string;
}

/**
 * Represents a character extracted from a novel.
 */
export interface MaterialCharacter {
  /** Unique identifier for the character */
  id: string;
  /** ID of the parent material library */
  novel_id: string;
  /** Character name */
  name: string;
  /** Alternative names or nicknames */
  aliases?: string[];
  /** Character description and traits */
  description?: string;
  /** ID of the chapter where the character first appears */
  first_appearance_chapter_id?: number | null;
  /** Chapter number where character first appears */
  first_appearance_chapter?: number | null;
  /** UTC timestamp of creation */
  created_at: string;
}

/**
 * Represents a story arc extracted from a novel.
 */
export interface MaterialStory {
  /** Unique identifier for the story */
  id: string;
  /** ID of the parent material library */
  novel_id: string;
  /** Story arc title */
  title: string;
  /** Story synopsis or summary */
  synopsis: string;
  /** Chapter range covered by this story (e.g., "1-5") */
  chapter_range?: string;
  /** Type classification of the story */
  story_type?: string;
  /** Primary objective of the story arc */
  core_objective?: string | null;
  /** Central conflict of the story arc */
  core_conflict?: string | null;
  /** Themes explored in this story */
  themes?: string | null;
  /** UTC timestamp of creation */
  created_at: string;
}

/**
 * Represents a plot point within a chapter.
 */
export interface MaterialPlot {
  /** Unique identifier for the plot */
  id: number;
  /** ID of the parent chapter */
  chapter_id: number;
  /** Sequential index within the chapter */
  index: number;
  /** Type classification of the plot point */
  plot_type: string;
  /** Description of the plot point */
  description: string;
  /** Characters involved in this plot point */
  characters: string[] | null;
}

/**
 * Represents a storyline spanning multiple chapters.
 */
export interface MaterialStoryLine {
  /** Unique identifier for the storyline */
  id: number;
  /** ID of the parent material library */
  novel_id: number;
  /** Storyline title */
  title: string;
  /** Storyline description */
  description: string | null;
  /** Main characters in this storyline */
  main_characters: string[] | null;
  /** Themes explored in this storyline */
  themes: string[] | null;
  /** Number of story segments in this storyline */
  stories_count: number;
  /** UTC timestamp of creation */
  created_at: string;
}

/**
 * Represents a relationship between two characters.
 */
export interface MaterialCharacterRelationship {
  /** Unique identifier for the relationship */
  id: number;
  /** ID of the first character */
  character_a_id: number;
  /** Name of the first character */
  character_a_name: string;
  /** ID of the second character */
  character_b_id: number;
  /** Name of the second character */
  character_b_name: string;
  /** Type of relationship (e.g., "friend", "enemy", "family") */
  relationship_type: string;
  /** Sentiment classification (e.g., "positive", "negative") */
  sentiment: string | null;
  /** Description of the relationship */
  description: string | null;
}

/**
 * Represents a faction or group in the story world.
 */
export interface Faction {
  /** Name of the faction */
  name: string;
  /** Description of the faction */
  description?: string;
  /** Leader of the faction */
  leader?: string;
  /** Territory controlled by the faction */
  territory?: string;
  /** Additional properties */
  [key: string]: unknown;
}

/**
 * Represents an evolution stage in a golden finger's history.
 */
export interface EvolutionHistoryItem {
  /** Evolution stage name */
  stage?: string;
  /** Chapter where this evolution occurred */
  chapter?: number | string;
  /** Description of the evolution */
  description?: string;
  /** Timestamp of the evolution */
  timestamp?: string;
  /** Additional properties */
  [key: string]: unknown;
}

/**
 * Represents a golden finger (特殊能力/作弊器) extracted from a novel.
 */
export interface MaterialGoldenFinger {
  /** Unique identifier for the golden finger */
  id: number;
  /** ID of the parent material library */
  novel_id: number;
  /** Name of the golden finger */
  name: string;
  /** Type classification */
  type: string;
  /** Description of the ability */
  description: string | null;
  /** Chapter ID where it first appears */
  first_appearance_chapter_id: number | null;
  /** History of ability evolution */
  evolution_history: EvolutionHistoryItem[] | null;
  /** UTC timestamp of creation */
  created_at: string;
}

/**
 * Represents the worldbuilding elements of a novel.
 */
export interface MaterialWorldView {
  /** Unique identifier for the worldview */
  id: number;
  /** ID of the parent material library */
  novel_id: number;
  /** Power/cultivation system description */
  power_system: string | null;
  /** World structure description */
  world_structure: string | null;
  /** Key factions in the world */
  key_factions: Faction[] | null;
  /** Special rules or laws of the world */
  special_rules: string | null;
  /** UTC timestamp of creation */
  created_at: string;
  /** UTC timestamp of last update */
  updated_at: string;
}

/**
 * Represents a node in the material tree structure.
 */
export interface MaterialTreeNode {
  /** Unique identifier for the node */
  id: string;
  /** Type of content (chapter, character, or story) */
  type: 'chapter' | 'character' | 'story';
  /** Display title */
  title: string;
  /** Child nodes in the tree */
  children?: MaterialTreeNode[];
  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

// ==================== Material Bridge Types ====================

/**
 * Summary item for a material library in the reference panel.
 */
export interface LibrarySummaryItem {
  /** Unique identifier for the library */
  id: number;
  /** Title of the novel */
  title: string;
  /** Processing status */
  status: string | null;
  /** Counts of extracted entities */
  counts: {
    /** Number of characters */
    characters: number;
    /** Number of worldview elements */
    worldview: number;
    /** Number of golden fingers */
    golden_fingers: number;
    /** Number of storylines */
    storylines: number;
    /** Number of stories */
    stories: number;
    /** Number of character relationships */
    relationships: number;
  };
}

/** Type of material entity for import operations */
export type MaterialEntityType = 'characters' | 'worldview' | 'goldenfingers' | 'storylines' | 'stories' | 'relationships';

/**
 * Basic item representing a material entity.
 */
export interface MaterialEntityItem {
  /** Unique identifier for the entity */
  id: number;
  /** Name or title of the entity */
  name: string;
  /** Brief summary of the entity */
  summary?: string;
}

/**
 * Response from the material preview endpoint.
 */
export interface MaterialPreviewResponse {
  /** Title for the preview */
  title: string;
  /** Formatted markdown content */
  markdown: string;
  /** Title of the source novel */
  novel_title: string;
  /** Suggested file type for import */
  suggested_file_type: string;
  /** Suggested folder name for organization */
  suggested_folder_name: string;
  /** Suggested file name */
  suggested_file_name: string;
}

/**
 * Request payload for importing a material entity to a project.
 */
export interface MaterialImportRequest {
  /** Target project ID */
  project_id: string;
  /** Source material library ID */
  novel_id: number;
  /** Type of entity to import */
  entity_type: MaterialEntityType;
  /** ID of the specific entity */
  entity_id: number;
  /** Custom file name (optional) */
  file_name?: string;
  /** Target folder ID (optional) */
  target_folder_id?: string;
}

/**
 * Response from a material import operation.
 */
export interface MaterialImportResponse {
  /** ID of the created file */
  file_id: string;
  /** Title of the imported file */
  title: string;
  /** Name of the containing folder */
  folder_name: string;
  /** File type assigned */
  file_type: string;
}

/**
 * Item for batch import operations.
 */
export interface BatchImportItem {
  /** Source material library ID */
  novel_id: number;
  /** Type of entity to import */
  entity_type: MaterialEntityType;
  /** ID of the specific entity */
  entity_id: number;
}

/**
 * Response from a batch import operation.
 */
export interface BatchImportResponse {
  /** Successfully imported items */
  results: MaterialImportResponse[];
  /** Number of failed imports */
  failed_count: number;
}

/**
 * Response from a material upload operation.
 */
export interface MaterialUploadResponse {
  /** ID of the created material library */
  novel_id: string;
  /** Status message */
  message: string;
  /** Processing status */
  status: string;
}

/**
 * Search result item from material search.
 */
export interface MaterialSearchResult {
  /** ID of the source material library */
  novel_id: number;
  /** Title of the source novel */
  novel_title: string;
  /** Type of the matched entity */
  entity_type: MaterialEntityType;
  /** ID of the matched entity */
  entity_id: number;
  /** Name/title of the matched entity */
  name: string;
}

// ==================== API Client ====================

/**
 * Materials API client for interacting with the material library service.
 *
 * Provides methods for uploading novels, checking decomposition status,
 * retrieving extracted content, and importing materials to projects.
 */
export const materialsApi = {
  /**
   * Get list of all material libraries for the current user.
   *
   * @returns Promise resolving to array of MaterialNovel objects
   */
  list: () =>
    api.get<MaterialNovel[]>("/api/v1/materials/list"),

  /**
   * Get details of a specific material library.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to MaterialNovel object
   */
  get: (novelId: string) =>
    api.get<MaterialNovel>(`/api/v1/materials/${novelId}`),

  /**
   * Upload a novel file for decomposition.
   *
   * The file will be processed asynchronously. Use get() to poll
   * for completion. Supports .txt uploads only.
   *
   * @param file - The novel file to upload
   * @param title - Optional custom title (defaults to filename)
   * @returns Promise resolving to upload response with novel_id
   * @throws ApiError if upload fails or file format is unsupported
   */
  upload: async (file: globalThis.File, title?: string): Promise<MaterialUploadResponse> => {
    const entryAccess = getAccessToken();
    const entryRefresh = localStorage.getItem("refresh_token");
    const formData = new FormData();
    formData.append("file", file);

    const query = new URLSearchParams();
    if (title) {
      query.set("title", title);
    }
    const uploadUrl = `${getApiBase()}/api/v1/materials/upload${query.toString() ? `?${query.toString()}` : ""}`;

    const fetchOnce = (accessToken: string | null): Promise<Response> => fetch(uploadUrl, {
      method: "POST",
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
      body: formData,
    });

    let response = await fetchOnce(entryAccess);
    if (response.status === 401 && entryAccess && entryRefresh) {
      let ownedSession = resolveOwnedAuthSession(entryAccess, entryRefresh);
      if (ownedSession?.accessToken && ownedSession.refreshToken) {
        if (ownedSession.accessToken === entryAccess && ownedSession.refreshToken === entryRefresh) {
          const refreshed = await tryRefreshToken();
          ownedSession = refreshed ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
        }
        if (ownedSession?.accessToken && ownedSession.refreshToken) {
          response = await fetchOnce(ownedSession.accessToken);
        }
      }
    }

    if (!response.ok) {
      let errorMessage = "ERR_MATERIAL_UPLOAD_FAILED";
      try {
        const errorData = await response.json();
        errorMessage = resolveApiErrorMessage(errorData, errorMessage);
      } catch {
        // Could not parse error response
      }
      throw new ApiError(response.status, errorMessage);
    }

    return response.json();
  },

  /**
   * Delete a material library and all associated data.
   *
   * This operation is irreversible and removes all chapters, characters,
   * storylines, and other extracted content.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving when deletion is complete
   */
  delete: (novelId: string) =>
    api.delete(`/api/v1/materials/${novelId}`),

  /**
   * Retry a failed decomposition task.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to success message
   */
  retry: (novelId: string) =>
    api.post<{ message: string }>(`/api/v1/materials/${novelId}/retry`),

  /**
   * Get hierarchical tree structure of material content.
   *
   * Returns an organized view of chapters, characters, and story arcs
   * suitable for display in a tree component.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to tree structure with nodes
   */
  getTree: (novelId: string) =>
    api.get<{ tree: MaterialTreeNode[] }>(`/api/v1/materials/${novelId}/tree`),

  /**
   * Get detailed chapter content.
   *
   * @param novelId - The unique identifier of the material library
   * @param chapterId - The unique identifier of the chapter
   * @returns Promise resolving to MaterialChapter with full content
   */
  getChapter: (novelId: string, chapterId: string) =>
    api.get<MaterialChapter>(`/api/v1/materials/${novelId}/chapters/${chapterId}`),

  /**
   * Get all characters extracted from a material library.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to array of MaterialCharacter objects
   */
  getCharacters: (novelId: string) =>
    api.get<MaterialCharacter[]>(`/api/v1/materials/${novelId}/characters`),

  /**
   * Get all story arcs extracted from a material library.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to array of MaterialStory objects
   */
  getStories: (novelId: string) =>
    api.get<MaterialStory[]>(`/api/v1/materials/${novelId}/stories`),

  /**
   * Get all plot points from a material library.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to array of MaterialPlot objects
   */
  getPlots: (novelId: string) =>
    api.get<MaterialPlot[]>(`/api/v1/materials/${novelId}/plots`),

  /**
   * Get all storylines from a material library.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to array of MaterialStoryLine objects
   */
  getStoryLines: (novelId: string) =>
    api.get<MaterialStoryLine[]>(`/api/v1/materials/${novelId}/storylines`),

  /**
   * Get all character relationships from a material library.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to array of MaterialCharacterRelationship objects
   */
  getRelationships: (novelId: string) =>
    api.get<MaterialCharacterRelationship[]>(`/api/v1/materials/${novelId}/relationships`),

  /**
   * Get all golden fingers from a material library.
   *
   * Golden fingers are special abilities or advantages possessed by
   * the protagonist in web novels.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to array of MaterialGoldenFinger objects
   */
  getGoldenFingers: (novelId: string) =>
    api.get<MaterialGoldenFinger[]>(`/api/v1/materials/${novelId}/goldenfingers`),

  /**
   * Get worldbuilding elements from a material library.
   *
   * @param novelId - The unique identifier of the material library
   * @returns Promise resolving to MaterialWorldView or null if not extracted
   */
  getWorldView: (novelId: string) =>
    api.get<MaterialWorldView | null>(`/api/v1/materials/${novelId}/worldview`),

  // ==================== Material Bridge APIs ====================

  /**
   * Get summary of all completed material libraries for the reference panel.
   *
   * Returns libraries with entity counts for quick browsing.
   *
   * @returns Promise resolving to array of LibrarySummaryItem objects
   */
  getLibrarySummary: () =>
    api.get<LibrarySummaryItem[]>("/api/v1/materials/library-summary"),

  /**
   * Search materials across all libraries.
   *
   * Searches through characters, worldbuilding, golden fingers,
   * storylines, and relationships.
   *
   * @param query - Search query string
   * @returns Promise resolving to array of MaterialSearchResult objects
   */
  searchMaterials: (query: string) =>
    api.get<MaterialSearchResult[]>(`/api/v1/materials/search?q=${encodeURIComponent(query)}`),

  /**
   * Get formatted markdown preview for a material entity.
   *
   * Returns a markdown representation of the entity suitable for
   * display or import as a file.
   *
   * @param novelId - The unique identifier of the material library
   * @param entityType - Type of entity to preview
   * @param entityId - ID of the specific entity
   * @returns Promise resolving to preview with formatted markdown
   */
  getPreview: (novelId: number, entityType: MaterialEntityType, entityId: number) =>
    api.get<MaterialPreviewResponse>(
      `/api/v1/materials/${novelId}/${entityType}/${entityId}/preview`
    ),

  /**
   * Import a material entity as a project file.
   *
   * Creates a new file in the specified project with content
   * derived from the material entity.
   *
   * @param request - Import request with project and entity details
   * @returns Promise resolving to import result with file_id
   */
  importToProject: (request: MaterialImportRequest) =>
    api.post<MaterialImportResponse>("/api/v1/materials/import", request),

  /**
   * Batch import multiple materials to a project.
   *
   * Imports multiple entities in a single request for efficiency.
   *
   * @param projectId - Target project ID
   * @param items - Array of items to import
   * @returns Promise resolving to batch results with success/failure counts
   */
  batchImport: (projectId: string, items: BatchImportItem[]) =>
    api.post<BatchImportResponse>("/api/v1/materials/batch-import", {
      project_id: projectId,
      items,
    }),
};

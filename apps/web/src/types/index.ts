// ==================== Core Models ====================

export type ProjectType = "novel" | "short" | "screenplay";

export interface Project {
  id?: string;
  name: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
  // Project type: novel, short, screenplay
  project_type?: ProjectType;
  // Project status fields for AI context awareness
  summary?: string;        // Project summary/background
  current_phase?: string;  // Current writing phase
  writing_style?: string;  // Writing style guidelines
  notes?: string;          // Notes for AI assistant
}

/**
 * Request body for patching project status fields
 */
export interface PatchProjectRequest {
  summary?: string;
  current_phase?: string;
  writing_style?: string;
  notes?: string;
  expected_notes?: string;
}

// ==================== File Model (Unified) ====================

/**
 * File types supported in the system
 */
export type FileType =
  | "folder"
  | "outline"
  | "draft"
  | "document"
  | "character"
  | "lore"
  | "material"
  | "script";  // Script content (for screenplay)

/**
 * Unified File model used by AI agent.
 * All project content (outlines, drafts, characters, lores, etc.) is stored as Files.
 */
export interface File {
  id: string;
  project_id: string;
  title: string;
  content: string;
  file_type: FileType;
  parent_id: string | null;
  order: number;
  file_metadata: string | null; // JSON string
  created_at: string;
  updated_at: string;
  /**
   * Only set on `PUT /files/{id}` responses. `true` means the content WAS saved
   * but the version-history quota is full, so no version snapshot was created.
   * Surface it as an upgrade hint, never as a save failure.
   */
  version_quota_exceeded?: boolean;
}

/**
 * Outcome of a batch `edit_file` call, carried by the `file_edit_end` event.
 *
 * `edit_file` can partially fail when `continue_on_error` is on: looking only
 * at `edits_applied` renders "1 of 3 succeeded" as a plain success, so the user
 * never learns the other two edits were never made.
 */
export interface FileEditOutcome {
  failedCount: number;
  partialSuccess: boolean;
  allFailed: boolean;
  warnings: string[];
}

/**
 * File tree node for hierarchical display
 */
export interface FileTreeNode {
  id: string;
  title: string;
  file_type: string;
  parent_id: string | null;
  order: number;
  content?: string;
  metadata: Record<string, unknown> | null;
  children: FileTreeNode[];
}

/**
 * Tree node type for UI
 */
export type TreeNodeType = FileType;

/**
 * Selected item for editor
 */
export interface SelectedItem {
  id: string;
  type: TreeNodeType;
  title: string;
}

// ==================== Search ====================

// ==================== File Version (New) ====================

/**
 * File version for tracking individual file changes
 */
export interface FileVersion {
  id: string;
  file_id: string;
  project_id: string;
  version_number: number;
  is_base_version: boolean;
  word_count: number;
  char_count: number;
  change_type: "create" | "edit" | "ai_edit" | "restore" | "auto_save";
  change_source: "user" | "ai" | "system";
  change_summary?: string;
  lines_added: number;
  lines_removed: number;
  created_at: string;
}

/**
 * Response for file version list
 */
export interface FileVersionListResponse {
  versions: FileVersion[];
  total: number;
  file_id: string;
  file_title: string;
  /**
   * First page only: the listed version whose content equals the live file
   * text, or null when the file has edits that are not in history yet.
   * Absent from older servers; treat a missing value as "unknown".
   */
  current_version_number?: number | null;
}

/**
 * Response for version content
 */
export interface VersionContentResponse {
  file_id: string;
  version_number: number;
  content: string;
  word_count: number;
  char_count: number;
  created_at: string;
}

/**
 * Version comparison result
 */
export interface VersionComparison {
  file_id: string;
  version1: {
    number: number;
    created_at: string | null;
    change_type: string | null;
    change_source: string | null;
    word_count: number;
  };
  version2: {
    number: number;
    created_at: string | null;
    change_type: string | null;
    change_source: string | null;
    word_count: number;
  };
  unified_diff: string;
  html_diff: DiffLine[];
  stats: {
    lines_added: number;
    lines_removed: number;
    word_diff: number;
  };
}

/**
 * Single line in HTML diff
 */
export interface DiffLine {
  type: "equal" | "added" | "removed";
  old_line: number | null;
  new_line: number | null;
  content: string;
}

// ==================== Agent Context Items ====================

export type ContextPriority =
  | "critical"
  | "constraint"
  | "relevant"
  | "inspiration";

/**
 * Context item assembled by backend for the agent prompt.
 *
 * NOTE: This is used for UI display only (sources/citations panel).
 */
export interface AgentContextItem {
  id: string;
  type: string; // outline/draft/character/lore/snippet/timeline/material...
  title: string;
  content: string;
  relevance_score?: number | null;
  priority?: ContextPriority;
  metadata?: Record<string, unknown>;
}

// ==================== SSE Event Types ====================

export type SSEEventType =
  | "thinking"
  | "thinking_content"
  | "context"
  | "content"
  | "content_start"
  | "content_end"
  | "tool_call"
  | "tool_result"
  | "file_created"
  | "file_content"
  | "file_content_end"
  | "file_edit_start"
  | "file_edit_applied"
  | "file_edit_end"
  | "skill_matched"
  | "iteration_exhausted"
  | "workflow_stopped"
  | "session_started"
  | "steering_received"
  // Earlier conversation was summarized before this round (single loop agent).
  | "history_compacted"
  | "done"
  | "error"
  // Sent after the terminal frame, only once the backend actually refunded the round.
  | "quota_refunded";

/**
 * Which layer stopped the round. empty_file_correction: a created file never got its text
 * (it was removed again, or is left unfinished because it could not be removed safely).
 * unexecuted_tool_text: the reply wrote a complete tool call out as text and it never ran.
 */
export type IterationExhaustedLayer = "collaboration" | "tool_call" | "empty_file_correction" | "unexecuted_tool_text";

/** A stop with a file still waiting for its text: titles of the files left unfinished / removed again (never their text). */
export interface IterationExhaustedFiles {
  unfinishedFiles?: string[];
  rolledBackFiles?: string[];
}

export interface SSEIterationExhaustedData {
  layer: IterationExhaustedLayer;
  iterations_used: number;
  max_iterations: number;
  reason: string;
  last_agent?: string;
  /** "wall_clock" when the run stopped near the time limit (the work may already be done) */
  limit?: string;
  unfinished_files?: { id?: string; title?: string }[];
  rolled_back_files?: { id?: string; title?: string }[];
}

export interface SSEWorkflowStoppedData {
  reason:
    | "clarification_needed"
    | "error"
    | "user_cancelled"
    | "user_stopped"
    | "invalid_handoff"
    | (string & {});
  agent_type: string;
  message: string;
  /**
   * Optional clarification payload (when reason === "clarification_needed").
   * Backend may provide the same string in `message` for backward compatibility.
   */
  question?: string;
  /** Optional context to help the user answer the clarification. */
  context?: string;
  /** Optional list of concrete information points the user should provide. */
  details?: string[];
}

export interface SSEEvent {
  type: SSEEventType;
  data: SSEIterationExhaustedData | SSEWorkflowStoppedData | Record<string, unknown>;
}

// ==================== Agent Stream State ====================

export interface ToolCall {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: "pending" | "success" | "error";
  result?: Record<string, unknown>;
  error?: string;
}

export interface AgentStreamState {
  isStreaming: boolean;
  isThinking: boolean;
  content: string;
  error: string | null;
  errorCode?: string | null;
  retryable: boolean;
}

// ==================== Agent Request ====================

export interface AgentRequest {
  project_id: string;
  message: string;
  session_id?: string;
  selected_text?: string;
  metadata?: Record<string, unknown>;
  /** Skills explicitly selected by the user for this message (max 3). */
  selected_skill_ids?: string[];
}

// ==================== Diff Review Types ====================

/**
 * Operation type for edit actions
 */
export type EditOperation = 
  | 'replace' 
  | 'insert_after' 
  | 'insert_before' 
  | 'append' 
  | 'prepend' 
  | 'delete';

/**
 * A single pending edit waiting for user review
 */
export interface PendingEdit {
  id: string;
  op: EditOperation;
  oldText?: string;
  newText?: string;
  status: 'pending' | 'accepted' | 'rejected';
}

/**
 * State for diff review mode in the editor
 */
export interface DiffReviewState {
  isReviewing: boolean;
  fileId: string;
  originalContent: string;    // Content before AI edits
  modifiedContent: string;    // Content after AI edits  
  pendingEdits: PendingEdit[];
  /**
   * What opened the review: a save/sync met a newer copy (`conflict`)
   * or a natural-polish result (`polish`). Absent for other callers.
   */
  origin?: DiffReviewOrigin;
}

export type DiffReviewOrigin = 'conflict' | 'polish';

/**
 * Response for rollback operation
 */
export interface RollbackResponse {
  success: boolean;
  message: string;
  file_id: string;
  restored_version: number;
  new_version_number: number | null;
  snapshot_created: boolean;
  version_quota_exceeded: boolean;
  /** The chat edit card this undo came from was marked undone (absent on older servers). */
  undo_recorded?: boolean;
}

/** Immutable provenance required to undo one specific AI file edit. */
export interface FileEditUndoTarget {
  fileId: string;
  beforeVersionNumber: number;
  expectedAfterUpdatedAt: string;
  /** SHA-256 (hex) of the text this edit wrote; absent on older cards and servers. */
  expectedAfterContentSha256?: string;
  /** The saved assistant message and tool call of the card, so the server can mark that card undone. */
  chatMessageId?: string;
  toolUseId?: string;
}

// ==================== Text Quote ====================

/**
 * Text quote for referencing selected text from editor in AI chat
 */
export interface TextQuote {
  id: string;
  text: string;
  fileId: string;
  fileTitle: string;
  createdAt: Date;
}

// ==================== Skill Types ====================

/**
 * Skill source type
 */
export type SkillSource = "builtin" | "user" | "added";

/**
 * Skill model for display
 */
export interface Skill {
  id: string;
  name: string;
  description: string | null;
  triggers: string[];
  /** Empty for added official skills (see instructions_hidden) */
  instructions: string;
  /** true for added official skills: the backend does not return their instructions */
  instructions_hidden?: boolean;
  source: SkillSource;
  is_active: boolean;
  /** Number of bundled resource files (references/, assets/) */
  resource_count?: number;
  /** Review status of the public copy after sharing; null when not shared */
  share_status?: SkillShareStatus | null;
  created_at?: string;
  updated_at?: string;
}

/**
 * Review status of a shared user skill's public copy
 */
export type SkillShareStatus = "pending" | "approved" | "unpublished";

/**
 * A text resource file bundled with a skill (under references/ or assets/)
 */
export interface SkillResource {
  path: string;
  size: number;
  updated_at: string;
}

/**
 * Content of a single skill resource file
 */
export interface SkillResourceContent {
  path: string;
  content: string;
}

/**
 * Response of importing a skill package (.zip or SKILL.md)
 */
export interface ImportSkillResponse {
  skill: Skill;
  /** Files dropped during import (scripts, unsupported types, ...) */
  warnings: string[];
}

/**
 * Request for creating a skill
 */
export interface CreateSkillRequest {
  name: string;
  description?: string;
  triggers: string[];
  instructions: string;
}

/**
 * Request for updating a skill
 */
export interface UpdateSkillRequest {
  name?: string;
  description?: string;
  triggers?: string[];
  instructions?: string;
  is_active?: boolean;
}

// ==================== Public Skill Types ====================

/**
 * Public skill status
 */
export type PublicSkillStatus = "pending" | "approved" | "rejected" | "unpublished";

/**
 * Public skill source
 */
export type PublicSkillSource = "official" | "community";

/**
 * Public skill model
 */
export interface PublicSkill {
  id: string;
  name: string;
  description: string | null;
  instructions: string;
  category: string;
  tags: string[];
  source: PublicSkillSource;
  author_id: string | null;
  author_name?: string;
  status: PublicSkillStatus;
  add_count: number;
  created_at: string;
  is_added?: boolean;
  /** List responses carry only a preview of `instructions`; true when it was cut */
  instructions_truncated?: boolean;
  /** true for official skills: `instructions` is empty and is not available to users */
  instructions_hidden?: boolean;
}

/**
 * User added skill from public library
 */
export interface AddedSkill {
  id: string;
  public_skill_id: string;
  name: string;
  description: string | null;
  instructions: string;
  /** true for official skills: `instructions` is empty and is not available to users */
  instructions_hidden?: boolean;
  category: string;
  source: "added";
  is_active: boolean;
  added_at: string;
}

/**
 * My skills response
 */
export interface MySkillsResponse {
  user_skills: Skill[];
  added_skills: AddedSkill[];
  total: number;
}

/**
 * Public skill list response
 */
export interface PublicSkillListResponse {
  skills: PublicSkill[];
  total: number;
  page: number;
  page_size: number;
}

/**
 * Skill category
 */
export interface SkillCategory {
  name: string;
  count: number;
}

// ==================== Agent API Key Types ====================

/**
 * Agent API Key model (matches backend ApiKeyResponse)
 */
export interface AgentApiKey {
  id: string;
  name: string;
  description?: string;
  key_prefix: string;
  scopes: string[];
  project_ids?: string[];
  is_active: boolean;
  last_used_at?: string;
  expires_at?: string;
  request_count: number;
  created_at: string;
  updated_at: string;
}

/**
 * Request for creating an Agent API Key (matches backend CreateApiKeyRequest)
 */
export interface CreateAgentApiKeyRequest {
  name: string;
  description?: string;
  scopes?: string[];
  project_ids?: string[];
  expires_in_days?: number;
}

/**
 * Response for creating an Agent API Key (matches backend ApiKeyWithSecretResponse)
 */
export interface CreateAgentApiKeyResponse {
  id: string;
  name: string;
  description?: string;
  key: string;
  key_prefix: string;
  scopes: string[];
  project_ids?: string[];
  is_active: boolean;
  expires_at?: string;
  created_at: string;
}

/**
 * Response for regenerating an Agent API Key (matches backend RegenerateKeyResponse)
 */
export interface RegenerateAgentApiKeyResponse {
  key: string;
}

/**
 * Agent API Key list response (matches backend ApiKeyListResponse)
 */
export interface AgentApiKeyListResponse {
  keys: AgentApiKey[];
  total: number;
}

// ==================== Inspiration ====================

/**
 * Inspiration source type
 */
export type InspirationSource = "official" | "community";

/**
 * Inspiration status
 */
export type InspirationStatus = "pending" | "approved" | "rejected";

/**
 * Inspiration model - project templates for discovery
 */
export interface Inspiration {
  id: string;
  name: string;
  description: string | null;
  cover_image: string | null;
  project_type: ProjectType;
  tags: string[];
  source: InspirationSource;
  author_id: string | null;
  original_project_id: string | null;
  copy_count: number;
  is_featured: boolean;
  created_at: string;
}

/**
 * Inspiration detail with file preview
 */
export interface InspirationDetail extends Inspiration {
  file_preview: Array<{
    title: string;
    file_type: FileType;
    has_content: boolean;
  }>;
}

/**
 * Inspiration list response
 */
export interface InspirationListResponse {
  inspirations: Inspiration[];
  total: number;
  page: number;
  page_size: number;
}

/**
 * Copy inspiration request
 */
export interface CopyInspirationRequest {
  project_name?: string;
}

/**
 * Copy inspiration response
 */
export interface CopyInspirationResponse {
  success: boolean;
  message: string;
  project_id: string | null;
  project_name: string | null;
}

/**
 * Submit inspiration request
 */
export interface SubmitInspirationRequest {
  project_id: string;
  name?: string;
  description?: string;
  cover_image?: string;
  tags?: string[];
}

/**
 * Submit inspiration response
 */
export interface SubmitInspirationResponse {
  success: boolean;
  message: string;
  inspiration_id: string;
  status: InspirationStatus;
}

/**
 * Current user's inspiration submission item
 */
export interface MyInspirationSubmission {
  id: string;
  name: string;
  description: string | null;
  tags: string[];
  status: InspirationStatus;
  copy_count: number;
  rejection_reason: string | null;
  created_at: string;
  reviewed_at: string | null;
}

/**
 * Current user's inspiration submissions response
 */
export interface MyInspirationSubmissionsResponse {
  items: MyInspirationSubmission[];
  total: number;
  page: number;
  page_size: number;
}

/** Writing progress shown on a project card (from GET /projects/progress). */
export interface ProjectProgress {
  project_id: string;
  /** Non-empty chapters (novel/short) or episodes (screenplay). */
  written_units: number;
  /** Editor word count across those files. */
  word_count: number;
  /** Outline/characters/setting exist but no prose yet. */
  framework_ready: boolean;
}

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

export interface TreeNode {
  id: string;
  name: string;
  type: TreeNodeType;
  children?: TreeNode[];
  data?: Record<string, unknown>;
}

/**
 * Selected item for editor
 */
export interface SelectedItem {
  id: string;
  type: TreeNodeType;
  title: string;
}

// ==================== Search ====================

// ==================== Snapshot / Version ====================

export interface Snapshot {
  id?: string;
  project_id: string;
  file_id?: string;
  data: string;
  description?: string;
  snapshot_type?: "auto" | "manual" | "pre_ai_edit" | "pre_rollback";
  version?: number;
  created_at?: string;
}

export interface SnapshotComparison {
  snapshot1: {
    id: string;
    created_at: string;
  };
  snapshot2: {
    id: string;
    created_at: string;
  };
  changes: {
    added: SnapshotComparisonFile[];
    removed: SnapshotComparisonFile[];
    modified: Array<{
      file_id: string;
      old_version?: number | null;
      new_version?: number | null;
      old_title?: string | null;
      new_title?: string | null;
      old_file_type?: string | null;
      new_file_type?: string | null;
      metadata_changes?: Partial<Record<SnapshotMetadataField, SnapshotMetadataChange>>;
    }>;
  };
}

type SnapshotMetadataField = "title" | "file_type" | "parent_id" | "order" | "file_metadata";

interface SnapshotMetadataChange {
  old: unknown;
  new: unknown;
}

interface SnapshotComparisonFile {
  file_id: string;
  title?: string | null;
  file_type?: string | null;
  parent_id?: string | null;
  order?: number | null;
  file_metadata?: string | null;
  version_number?: number | null;
  version_id?: string | null;
}

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

// ==================== Conflict Detection ====================

export type ConflictType =
  | "timeline_conflict"
  | "character_conflict"
  | "lore_conflict"
  | "logic_conflict"
  | "style_conflict"
  | "missing_foreshadowing"
  | "contradiction"
  | "pov_conflict"
  | "tense_conflict";

export interface Conflict {
  type: ConflictType;
  severity: "low" | "medium" | "high";
  title: string;
  description: string;
  location?: string;
  suggestions: string[];
  references: Record<string, unknown>[];
}

export type ApplyAction =
  | "insert"
  | "replace"
  | "new_snippet"
  | "reference_only";

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
  | "conflict"
  | "reference"
  | "file_created"
  | "file_content"
  | "file_content_end"
  | "file_edit_start"
  | "file_edit_applied"
  | "file_edit_end"
  | "skill_matched"
  | "skills_matched"
  | "agent_selected"
  | "iteration_exhausted"
  | "router_thinking"
  | "router_decided"
  | "handoff"
  | "workflow_stopped"
  | "workflow_complete"
  | "session_started"
  | "parallel_start"
  | "parallel_task_start"
  | "parallel_task_end"
  | "parallel_end"
  | "steering_received"
  | "done"
  | "error"
  // Sent after the terminal frame, only once the backend actually refunded the round.
  | "quota_refunded";

export interface SSEThinkingData {
  message: string;
  step?: string;
}

export interface SSEThinkingContentData {
  content: string;
  is_complete?: boolean;
}

export interface SSEContextData {
  items: AgentContextItem[];
  token_count?: number;
}

export interface SSEContentData {
  text: string;
  index?: number;
}

export interface SSEToolCallData {
  tool_use_id?: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}

export interface SSEToolResultData {
  tool_use_id?: string;
  tool_name: string;
  status: "success" | "error";
  data?: Record<string, unknown>;
  error?: string;
}

export interface SSEConflictData {
  type: ConflictType;
  severity: "low" | "medium" | "high";
  title: string;
  description: string;
  suggestions: string[];
}

export interface SSEErrorData {
  message: string;
  code?: string;
  retryable: boolean;
}

export interface SSEFileCreatedData {
  file_id: string;
  file_type: string;
  title: string;
}

export interface SSEFileContentData {
  file_id: string;
  chunk: string;
}

export interface SSEFileContentEndData {
  file_id: string;
}

export interface SSEFileEditStartData {
  file_id: string;
  title: string;
  total_edits: number;
  file_type?: string;
}

export interface SSEFileEditAppliedData {
  file_id: string;
  edit_index: number;
  op: string;
  old_preview?: string;
  new_preview?: string;
  success: boolean;
  error?: string;
}

export interface SSEFileEditEndData {
  file_id: string;
  edits_applied: number;
  new_length: number;
  new_content?: string;  // Full content after edits (for diff review)
  original_content?: string;  // Content before edits (for diff review)
  file_type?: string;
  title?: string;
}

export interface SSESkillMatchedData {
  skill_id: string;
  skill_name: string;
  matched_trigger: string;
}

export interface SSESkillsMatchedData {
  skills: Array<{
    id: string;
    name: string;
    trigger: string;
    confidence: number;
  }>;
  total_count: number;
}

export interface SSEAgentSelectedData {
  agent_type: string;
  agent_name: string;
  iteration?: number;
  max_iterations?: number;
  remaining?: number;
}

export interface SSEIterationExhaustedData {
  layer: "collaboration" | "tool_call";
  iterations_used: number;
  max_iterations: number;
  reason: string;
  last_agent?: string;
}

export interface SSERoutingMetadata {
  agent_type: string;
  workflow_type: string;
  reason: string;
  confidence: number;
}

export interface SSERouterDecidedData {
  initial_agent: string;
  workflow_plan: string;
  workflow_agents: string[];
  routing_metadata?: SSERoutingMetadata;
}

export interface SSEWorkflowEvaluationData {
  complete_score: number;
  clarification_score: number;
  consistency_score: number;
  decision_reason: string;
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
  confidence?: number;
  evaluation?: SSEWorkflowEvaluationData;
}

export interface SSEWorkflowCompleteData {
  reason: "task_complete";
  agent_type: string;
  message: string;
  confidence?: number;
  evaluation?: SSEWorkflowEvaluationData;
}

export interface SSEHandoffPacket {
  target_agent: string;
  reason: string;
  context?: string;
  completed?: string[];
  todo?: string[];
  evidence?: string[];
}

export interface SSEHandoffData {
  target_agent: string;
  reason: string;
  context?: string;
  handoff_packet?: SSEHandoffPacket;
}

export interface SSESessionStartedData {
  session_id: string;
}

export interface SSEParallelStartData {
  execution_id: string;
  task_count: number;
  task_descriptions: string[];
}

export interface SSEParallelTaskStartData {
  execution_id: string;
  task_id: string;
  task_type: string;
  description: string;
}

export interface SSEParallelTaskEndData {
  execution_id: string;
  task_id: string;
  status: "completed" | "failed";
  result_preview?: string;
  error?: string;
}

export interface SSEParallelEndData {
  execution_id: string;
  total_tasks: number;
  completed: number;
  failed: number;
  duration_ms: number;
}

export interface SSESteeringReceivedData {
  message_id: string;
  preview: string;
}

export interface SSEDoneData {
  apply_action?: ApplyAction;
  refs?: number[];
  assistant_message_id?: string;
  session_id?: string;
}

export interface SSEEvent {
  type: SSEEventType;
  data:
    | SSEThinkingData
    | SSEThinkingContentData
    | SSEContextData
    | SSEContentData
    | SSEToolCallData
    | SSEToolResultData
    | SSEConflictData
    | SSEErrorData
    | SSEDoneData
    | SSEFileCreatedData
    | SSEFileContentData
    | SSEFileContentEndData
    | SSEFileEditStartData
    | SSEFileEditAppliedData
    | SSEFileEditEndData
    | SSESkillMatchedData
    | SSESkillsMatchedData
    | SSEAgentSelectedData
    | SSEIterationExhaustedData
    | SSERouterDecidedData
    | SSEHandoffData
    | SSEWorkflowStoppedData
    | SSEWorkflowCompleteData
    | SSESessionStartedData
    | SSEParallelStartData
    | SSEParallelTaskStartData
    | SSEParallelTaskEndData
    | SSEParallelEndData
    | SSESteeringReceivedData
    | Record<string, unknown>;
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
  thinkingMessage: string;
  thinkingContent: string;
  content: string;
  conflicts: Conflict[];
  contextItems: AgentContextItem[];
  contextTokenCount?: number | null;
  error: string | null;
  errorCode?: string | null;
  retryable: boolean;
  applyAction: ApplyAction | null;
  refs: number[];
  toolCalls: ToolCall[];
}

// ==================== Agent Request/Response ====================

export interface AgentResponse {
  text: string;
  apply_action: ApplyAction;
  refs: number[];
  used_files: Record<string, unknown>[];
  used_snippets?: Record<string, unknown>[]; // Alias for used_files for backward compatibility
  conflicts: Conflict[];
  reasoning?: string;
}

export interface AgentRequest {
  project_id: string;
  message: string;
  session_id?: string;
  selected_text?: string;
  context_before?: string;
  context_after?: string;
  file_id?: string;
  outline_id?: string; // For backward compatibility
  metadata?: Record<string, unknown>;
  stream?: boolean;
  /** Skills explicitly selected by the user for this message (max 3). */
  selected_skill_ids?: string[];
}

export interface AgentExecutionResult {
  success: boolean;
  response: AgentResponse | null;
  context_data?: Record<string, unknown>;
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
}

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
}

/** Immutable provenance required to undo one specific AI file edit. */
export interface FileEditUndoTarget {
  fileId: string;
  beforeVersionNumber: number;
  expectedAfterUpdatedAt: string;
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
  instructions: string;
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

/**
 * Skill usage statistics
 */
export interface SkillUsageStats {
  total_triggers: number;
  builtin_count: number;
  user_count: number;
  top_skills: TopSkillItem[];
  daily_usage: DailyUsageItem[];
}

export interface TopSkillItem {
  skill_id: string;
  skill_name: string;
  skill_source: SkillSource;
  count: number;
}

export interface DailyUsageItem {
  date: string;
  count: number;
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

// ==================== Subscription Types ====================

/**
 * Subscription tier
 */
export type SubscriptionTier = "free" | "pro";

/**
 * Subscription status response from API
 */
export interface SubscriptionStatus {
  tier: SubscriptionTier;
  status: string;
  display_name: string;
  current_period_end: string | null;
  days_remaining: number | null;
  features: Record<string, unknown>;
}

/**
 * Quota usage response from API
 */
export interface QuotaResponse {
  ai_conversations: {
    used: number;
    limit: number;
    reset_at: string | null;
  };
  projects: {
    used: number;
    limit: number;
  };
  material_uploads?: {
    used: number;
    limit: number;
  };
  material_decompositions?: {
    used: number;
    limit: number;
  };
  skill_creates?: {
    used: number;
    limit: number;
  };
  inspiration_copies?: {
    used: number;
    limit: number;
  };
}

/**
 * Subscription history item
 */
export interface SubscriptionHistoryItem {
  id: string;
  action: string;
  plan_name: string;
  start_date: string;
  end_date: string | null;
  created_at: string;
}

/**
 * Redeem code request
 */
export interface RedeemCodeRequest {
  code: string;
}

/**
 * Redeem code response
 */
export interface RedeemCodeResponse {
  success: boolean;
  message: string;
  tier: string | null;
  duration_days: number | null;
}

// ==================== Points Types ====================

/**
 * Points balance response
 */
export interface PointsBalance {
  available: number;
  pending_expiration: number;
  nearest_expiration_date: string | null;
}

/**
 * Check-in response
 */
export interface CheckInResponse {
  success: boolean;
  points_earned: number;
  streak_days: number;
  message: string;
}

/**
 * Check-in status response
 */
export interface CheckInStatus {
  checked_in: boolean;
  streak_days: number;
  points_earned_today: number;
}

/**
 * Points transaction item
 */
export interface PointsTransaction {
  id: string;
  amount: number;
  balance_after: number;
  transaction_type: string;
  source_id: string | null;
  description: string | null;
  expires_at: string | null;
  is_expired: boolean;
  created_at: string;
}

/**
 * Transaction history response
 */
export interface TransactionHistoryResponse {
  transactions: PointsTransaction[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/**
 * Earn opportunity item
 */
export interface EarnOpportunity {
  type: string;
  points: number;
  description: string;
  is_completed: boolean;
  is_available: boolean;
}

/**
 * Redeem Pro request
 */
export interface RedeemProRequest {
  days: number;
}

/**
 * Redeem Pro response
 */
export interface RedeemProResponse {
  success: boolean;
  points_spent: number;
  pro_days: number;
  new_period_end: string;
}

// ============================================
// Agent Enhancement Event Types
// ============================================

/**
 * Session lifecycle event - provides session ID for steering
 */
export interface SessionStartedEvent {
  type: "session_started";
  session_id: string;
}

/**
 * Parallel execution events
 */
export interface ParallelStartEvent {
  type: "parallel_start";
  execution_id: string;
  /** Tasks actually executed this round (capped by the backend concurrency limit). */
  task_count: number;
  task_descriptions: string[];
  /** Tasks the model originally asked for, before the cap was applied. */
  requested_task_count?: number;
  /** Requested tasks dropped because of the cap; the model must re-issue them. */
  dropped_count?: number;
}

export interface ParallelTaskStartEvent {
  type: "parallel_task_start";
  execution_id: string;
  task_id: string;
  task_type: string;
  description: string;
}

export interface ParallelTaskEndEvent {
  type: "parallel_task_end";
  execution_id: string;
  task_id: string;
  status: "completed" | "failed";
  result_preview?: string;
  error?: string;
}

export interface ParallelEndEvent {
  type: "parallel_end";
  execution_id: string;
  total_tasks: number;
  completed: number;
  failed: number;
  duration_ms: number;
  requested_task_count?: number;
  dropped_count?: number;
}

/**
 * Steering event - triggered when steering message is received
 */
export interface SteeringReceivedEvent {
  type: "steering_received";
  message_id: string;
  preview: string;
}

/**
 * Union type for all new agent events
 */
export type AgentEnhancementEvent =
  | SessionStartedEvent
  | ParallelStartEvent
  | ParallelTaskStartEvent
  | ParallelTaskEndEvent
  | ParallelEndEvent
  | SteeringReceivedEvent;

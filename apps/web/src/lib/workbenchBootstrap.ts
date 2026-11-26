import type { QueryClient } from "@tanstack/react-query";
import * as apiModule from "./api";
import type { ProjectBootstrapResponse } from "./api";
import { ApiError } from "./apiClient";
import { getRecentMessages } from "./chatApi";
import { logger } from "./logger";
import { writeWorkbenchSwr } from "./workbenchSwrCache";
import type { File } from "../types";

export function isProjectBootstrapAvailable(): boolean {
  return (
    Object.prototype.hasOwnProperty.call(apiModule, "projectApi") &&
    typeof apiModule.projectApi?.getBootstrap === "function"
  );
}

export const BOOTSTRAP_DEFAULT_CHAT_LIMIT = 15;
export const BOOTSTRAP_HANDOFF_MAX_AGE_MS = 15_000;
export const WORKBENCH_FRESH_FILE_EVENT = "zenstory:workbench-fresh-file";

export interface WorkbenchFreshFileDetail {
  userId: string;
  projectId: string;
  file: File;
}

export interface ProjectBootstrapResult {
  data: ProjectBootstrapResponse;
  usedFallback: boolean;
  fileTreeReady: boolean;
  chatReady: boolean;
  /** Distinguishes definitive missing/invalid file from transient 5xx/network errors on legacy fallback. */
  initialFileStatus: "resolved" | "missing" | "transient_error";
}

export interface BootstrapHandoffEntry {
  userId: string;
  projectId: string;
  requestedFileId: string | null;
  startedAt: number;
  promise: Promise<ProjectBootstrapResult | null>;
  settled: boolean;
  fileTreeConsumed: boolean;
  chatConsumed: boolean;
}

export function workbenchBootstrapQueryKey(userId: string, projectId: string) {
  return ["workbench-bootstrap", userId, projectId] as const;
}

export function isBootstrapEndpointMissingError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status === 405) return true;
  // Distinguish an old backend missing `/bootstrap` (plain FastAPI 404) from a
  // new backend definitively reporting ERR_PROJECT_NOT_FOUND.
  return error.status === 404 && error.errorCode !== "ERR_PROJECT_NOT_FOUND";
}

export function dispatchWorkbenchFreshFile(detail: WorkbenchFreshFileDetail): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<WorkbenchFreshFileDetail>(WORKBENCH_FRESH_FILE_EVENT, { detail }));
}

export function isValidProjectBootstrapResponse(
  data: unknown,
  expectedProjectId?: string,
): data is ProjectBootstrapResponse {
  if (!data || typeof data !== "object") return false;
  const candidate = data as Partial<ProjectBootstrapResponse>;
  if (
    !candidate.project ||
    typeof candidate.project !== "object" ||
    typeof candidate.project.id !== "string" ||
    candidate.project.id.length === 0
  ) {
    return false;
  }
  if (expectedProjectId && candidate.project.id !== expectedProjectId) {
    return false;
  }
  if (!Array.isArray(candidate.files)) return false;
  if (!Array.isArray(candidate.recent_messages)) return false;
  if (
    candidate.initial_file !== undefined &&
    candidate.initial_file !== null &&
    typeof candidate.initial_file !== "object"
  ) {
    return false;
  }
  return true;
}

export async function fetchProjectBootstrapWithFallback(
  projectId: string,
  options: { fileId?: string | null; chatLimit?: number } = {},
): Promise<ProjectBootstrapResult> {
  const chatLimit = options.chatLimit ?? BOOTSTRAP_DEFAULT_CHAT_LIMIT;
  const fileId = options.fileId ?? null;

  try {
    const data = await apiModule.projectApi.getBootstrap(projectId, {
      fileId,
      chatLimit,
    });
    if (isValidProjectBootstrapResponse(data, projectId)) {
      return {
        data,
        usedFallback: false,
        fileTreeReady: true,
        chatReady: true,
        initialFileStatus: data.initial_file ? "resolved" : "missing",
      };
    }

    logger.warn(
      "[Bootstrap] /bootstrap returned invalid payload shape, falling back to parallel legacy endpoints",
      { projectId },
    );
  } catch (error) {
    if (!isBootstrapEndpointMissingError(error)) {
      throw error;
    }

    logger.log("[Bootstrap] /bootstrap unavailable (404/405), falling back to parallel legacy endpoints", {
      projectId,
    });
  }

  const [project, treeResult, chatResult, fileResult] = await Promise.all([
    apiModule.projectApi.get(projectId),
    apiModule.fileApi
      .getTree(projectId)
      .then((res) => ({ ok: true as const, files: res.tree || [] }))
      .catch(() => ({ ok: false as const, files: [] })),
    getRecentMessages(projectId, chatLimit + 1)
      .then((messages) => {
        const hasMore = messages.length > chatLimit;
        return {
          ok: true as const,
          messages: hasMore ? messages.slice(messages.length - chatLimit) : messages,
          hasMore,
        };
      })
      .catch(() => ({ ok: false as const, messages: [], hasMore: false })),
    fileId
      ? apiModule.fileApi
          .get(fileId)
          .then((file): { status: "resolved" | "missing"; file: File | null } => {
            const isDeleted = (file as { is_deleted?: boolean }).is_deleted === true;
            if (file.project_id !== projectId || isDeleted || (file.file_type as string) === "folder") {
              return { status: "missing", file: null };
            }
            return { status: "resolved", file };
          })
          .catch((fileErr): { status: "missing" | "transient_error"; file: null } => {
            if (fileErr instanceof ApiError && fileErr.status >= 400 && fileErr.status < 500) {
              return { status: "missing", file: null };
            }
            return { status: "transient_error", file: null };
          })
      : Promise.resolve<{ status: "missing"; file: null }>({ status: "missing", file: null }),
  ]);

  return {
    data: {
      project,
      files: treeResult.files,
      initial_file: fileResult.file,
      chat_session: null,
      recent_messages: chatResult.messages,
      has_more_messages: chatResult.hasMore,
    },
    usedFallback: true,
    fileTreeReady: treeResult.ok,
    chatReady: chatResult.ok,
    initialFileStatus: fileResult.status,
  };
}

export function evictProjectBootstrapHandoff(
  queryClient: QueryClient | undefined,
  userId: string | null | undefined,
  projectId: string | null | undefined,
): void {
  if (!queryClient || !userId || !projectId) return;
  queryClient.removeQueries({ queryKey: workbenchBootstrapQueryKey(userId, projectId), exact: true });
}

export function startProjectBootstrap(
  queryClient: QueryClient | undefined,
  options: {
    userId: string;
    projectId: string;
    fileId?: string | null;
    chatLimit?: number;
    now?: number;
  },
): Promise<ProjectBootstrapResult | null> {
  const { userId, projectId, fileId = null, chatLimit = BOOTSTRAP_DEFAULT_CHAT_LIMIT } = options;
  const now = options.now ?? Date.now();
  const key = workbenchBootstrapQueryKey(userId, projectId);

  if (queryClient) {
    const existing = queryClient.getQueryData<BootstrapHandoffEntry>(key);
    // Only deduplicate an in-flight (or freshly settled & unconsumed, e.g. StrictMode
    // remount) handoff; never serve a stale consumed bootstrap on A→B→A revisit.
    if (
      existing &&
      existing.userId === userId &&
      existing.projectId === projectId &&
      existing.requestedFileId === fileId &&
      now - existing.startedAt <= BOOTSTRAP_HANDOFF_MAX_AGE_MS &&
      (!existing.settled || (!existing.fileTreeConsumed && !existing.chatConsumed))
    ) {
      return existing.promise;
    }
  }

  let entry: BootstrapHandoffEntry | null = null;
  const settledPromise = fetchProjectBootstrapWithFallback(projectId, { fileId, chatLimit })
    .then((result) => {
      if (entry) entry.settled = true;
      // Persist user-scoped SWR cache only when the entry still belongs to the active query key.
      if (!queryClient || !entry || queryClient.getQueryData<BootstrapHandoffEntry>(key) === entry) {
        writeWorkbenchSwr(userId, projectId, {
          project: result.data.project,
          files: result.data.files,
          initial_file: result.data.initial_file,
        });
      }
      return result;
    })
    .catch((err) => {
      if (entry) entry.settled = true;
      throw err;
    });

  if (queryClient) {
    entry = {
      userId,
      projectId,
      requestedFileId: fileId,
      startedAt: now,
      // Suppress unhandled rejections on the shared promise for passive consumers (useFileTree/ChatPanel)
      // while ProjectEditor still observes rejections on `settledPromise`.
      promise: settledPromise.catch(() => null),
      settled: false,
      fileTreeConsumed: false,
      chatConsumed: false,
    };
    queryClient.setQueryData<BootstrapHandoffEntry>(key, entry);
  }

  return settledPromise;
}

function getFreshBootstrapEntry(
  queryClient: QueryClient | undefined,
  userId: string | null | undefined,
  projectId: string | null | undefined,
  now: number = Date.now(),
): BootstrapHandoffEntry | null {
  if (!queryClient || !userId || !projectId) return null;
  const key = workbenchBootstrapQueryKey(userId, projectId);
  const entry = queryClient.getQueryData<BootstrapHandoffEntry>(key);
  if (!entry) return null;
  if (
    entry.userId !== userId ||
    entry.projectId !== projectId ||
    now - entry.startedAt > BOOTSTRAP_HANDOFF_MAX_AGE_MS
  ) {
    queryClient.removeQueries({ queryKey: key, exact: true });
    return null;
  }
  return entry;
}

export function peekBootstrappedFileTree(
  queryClient: QueryClient | undefined,
  userId: string | null | undefined,
  projectId: string | null | undefined,
): BootstrapHandoffEntry | null {
  const entry = getFreshBootstrapEntry(queryClient, userId, projectId);
  if (!entry || entry.fileTreeConsumed) return null;
  return entry;
}

export function markBootstrappedFileTreeConsumed(
  queryClient: QueryClient | undefined,
  userId: string | null | undefined,
  projectId: string | null | undefined,
  entry: BootstrapHandoffEntry,
): void {
  if (!queryClient || !userId || !projectId) return;
  const current = queryClient.getQueryData<BootstrapHandoffEntry>(workbenchBootstrapQueryKey(userId, projectId));
  if (current === entry) {
    current.fileTreeConsumed = true;
  }
}

export function peekBootstrappedChat(
  queryClient: QueryClient | undefined,
  userId: string | null | undefined,
  projectId: string | null | undefined,
): BootstrapHandoffEntry | null {
  const entry = getFreshBootstrapEntry(queryClient, userId, projectId);
  if (!entry || entry.chatConsumed) return null;
  return entry;
}

export function markBootstrappedChatConsumed(
  queryClient: QueryClient | undefined,
  userId: string | null | undefined,
  projectId: string | null | undefined,
  entry: BootstrapHandoffEntry,
): void {
  if (!queryClient || !userId || !projectId) return;
  const current = queryClient.getQueryData<BootstrapHandoffEntry>(workbenchBootstrapQueryKey(userId, projectId));
  if (current === entry) {
    current.chatConsumed = true;
  }
}

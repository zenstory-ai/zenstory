import type { QueryClient } from "@tanstack/react-query";
import type { File } from "../types";

/**
 * Short-lived, one-shot handoff cache so ProjectEditor can pass a file it just
 * validated via `fileApi.get(id)` (last-opened restore or `?file=` deep link)
 * straight to Editor without a duplicate serial `GET /api/v1/files/{id}`.
 *
 * Strictly scoped by `(userId, projectId, fileId)` inside QueryClient (so
 * AuthIdentityQueryBoundary clears it on account switch) and removed on first
 * committed read so subsequent opens / refreshes always hit the live server.
 */
export const PRELOADED_FILE_MAX_AGE_MS = 10_000;

interface PreloadedFileEntry {
  file: File;
  fetchedAt: number;
}

export function preloadedFileQueryKey(
  userId: string | null,
  projectId: string,
  fileId: string,
) {
  return ["preloaded-file", userId ?? "anonymous", projectId, fileId] as const;
}

function hasCompleteFilePayload(file: File | null | undefined): file is File {
  return Boolean(
    file &&
    typeof file.id === "string" &&
    typeof file.project_id === "string" &&
    typeof file.content === "string",
  );
}

export function seedPreloadedFile(
  queryClient: QueryClient | undefined,
  userId: string | null,
  projectId: string,
  file: File,
  now: number = Date.now(),
): void {
  if (!queryClient || !hasCompleteFilePayload(file)) return;
  if (file.project_id !== projectId) return;
  queryClient.setQueryData<PreloadedFileEntry>(
    preloadedFileQueryKey(userId, projectId, file.id),
    { file, fetchedAt: now },
  );
}

export function peekPreloadedFile(
  queryClient: QueryClient | undefined,
  userId: string | null,
  projectId: string,
  fileId: string,
  now: number = Date.now(),
): File | null {
  if (!queryClient) return null;
  const key = preloadedFileQueryKey(userId, projectId, fileId);
  const entry = queryClient.getQueryData<PreloadedFileEntry>(key);
  if (!entry) return null;
  if (
    now - entry.fetchedAt > PRELOADED_FILE_MAX_AGE_MS ||
    entry.file.id !== fileId ||
    entry.file.project_id !== projectId ||
    !hasCompleteFilePayload(entry.file)
  ) {
    queryClient.removeQueries({ queryKey: key, exact: true });
    return null;
  }
  return entry.file;
}

export function consumePreloadedFile(
  queryClient: QueryClient | undefined,
  userId: string | null,
  projectId: string,
  fileId: string,
): void {
  if (!queryClient) return;
  queryClient.removeQueries({
    queryKey: preloadedFileQueryKey(userId, projectId, fileId),
    exact: true,
  });
}

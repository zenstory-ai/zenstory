/** Confirmed removals fence stale editor reads and delayed save/cleanup work. */
export const FILE_REMOVED_EVENT = "zenstory:file-removed";
export interface FileRemovedDetail { projectId: string; fileId: string }
const removed = new Set<string>();
const key = (projectId: string, fileId: string) => `${projectId}:${fileId}`;
export function isFileRemoved(projectId: string | null | undefined, fileId: string | null | undefined): boolean {
  return !!projectId && !!fileId && removed.has(key(projectId, fileId));
}
export function clearFileRemoved(projectId: string, fileId: string): void {
  removed.delete(key(projectId, fileId));
}
type IdTreeNode = { id: string; children?: ReadonlyArray<IdTreeNode> };
export function clearLiveTreeRemoved(projectId: string, nodes: ReadonlyArray<IdTreeNode>): void {
  for (const node of nodes) {
    removed.delete(key(projectId, node.id));
    if (node.children?.length) clearLiveTreeRemoved(projectId, node.children);
  }
}
export function notifyFileRemoved(projectId: string, fileId: string): void {
  removed.add(key(projectId, fileId));
  window.dispatchEvent(new CustomEvent<FileRemovedDetail>(FILE_REMOVED_EVENT, { detail: { projectId, fileId } }));
}

// The chat owns the one router leave guard. Editors contribute this additional
// reason to protect a deleted draft whose only remaining copy is in memory.
const unprotectedProjects = new Set<string>();
const protectionListeners = new Set<() => void>();
export function setUnprotectedDeletedDraft(projectId: string, active: boolean): void {
  if (unprotectedProjects.has(projectId) === active) return;
  if (active) unprotectedProjects.add(projectId);
  else unprotectedProjects.delete(projectId);
  protectionListeners.forEach(listener => listener());
}
export const hasAnyUnprotectedDeletedDraft = (): boolean => unprotectedProjects.size > 0;
export function subscribeDeletedDraftProtection(listener: () => void): () => void {
  protectionListeners.add(listener);
  return () => { protectionListeners.delete(listener); };
}

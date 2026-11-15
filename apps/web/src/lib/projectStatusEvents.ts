export const PROJECT_STATUS_UPDATED_EVENT = "zenstory:project-status-updated";

export interface ProjectStatusUpdatedEventDetail {
  projectId: string;
  updatedFields: string[];
}

export function dispatchProjectStatusUpdated(
  detail: ProjectStatusUpdatedEventDetail
): void {
  window.dispatchEvent(
    new CustomEvent<ProjectStatusUpdatedEventDetail>(
      PROJECT_STATUS_UPDATED_EVENT,
      { detail }
    )
  );
}

export function subscribeProjectStatusUpdated(
  listener: (detail: ProjectStatusUpdatedEventDetail) => void
): () => void {
  const handler = (event: Event) => {
    const customEvent = event as CustomEvent<ProjectStatusUpdatedEventDetail>;
    if (customEvent.detail) {
      listener(customEvent.detail);
    }
  };

  window.addEventListener(PROJECT_STATUS_UPDATED_EVENT, handler);
  return () => window.removeEventListener(PROJECT_STATUS_UPDATED_EVENT, handler);
}

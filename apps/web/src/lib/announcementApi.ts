import { api } from "./apiClient";
import type { AdminAnnouncement, Announcement, UpdateAnnouncementRequest } from "../types/announcement";

export const announcementApi = {
  getCurrent: () => api.get<Announcement | null>("/api/announcements/current"),
  getCurrentAdmin: () => api.get<AdminAnnouncement>("/api/admin/announcements/current"),
  updateCurrent: (request: UpdateAnnouncementRequest) =>
    api.put<AdminAnnouncement>("/api/admin/announcements/current", request),
};

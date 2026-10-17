export interface Announcement {
  title: string;
  description: string;
  action_label: string | null;
  action_url: string | null;
  version: number;
  updated_at: string | null;
}

export interface AdminAnnouncement extends Announcement {
  enabled: boolean;
  updated_by: string | null;
}

export interface UpdateAnnouncementRequest {
  title: string;
  description: string;
  action_label: string | null;
  action_url: string | null;
  enabled: boolean;
  expected_version: number;
  expected_updated_at: string | null;
}

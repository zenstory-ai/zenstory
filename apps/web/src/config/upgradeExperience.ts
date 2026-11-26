export type UpgradePromptSurface = "modal" | "toast" | "page";

export type UpgradePromptScenario =
  | "chat_quota_blocked"
  | "project_quota_blocked"
  | "file_version_quota_blocked"
  | "export_format_quota_blocked"
  | "material_upload_quota_blocked"
  | "material_decompose_quota_blocked"
  | "skill_create_quota_blocked"
  | "settings_subscription_upgrade"
  | "billing_header_upgrade";

export interface UpgradePromptDefinition {
  surface: UpgradePromptSurface;
  source: string;
  billingPath: string;
  pricingPath: string;
}

const UPGRADE_PROMPT_DEFINITIONS: Record<UpgradePromptScenario, UpgradePromptDefinition> = {
  chat_quota_blocked: {
    surface: "modal",
    source: "chat_quota_blocked",
    billingPath: "/dashboard/billing",
    pricingPath: "/pricing",
  },
  project_quota_blocked: {
    surface: "modal",
    source: "project_quota_blocked",
    billingPath: "/dashboard/billing",
    pricingPath: "/pricing",
  },
  file_version_quota_blocked: {
    surface: "modal",
    source: "file_version_quota_blocked",
    billingPath: "/dashboard/billing",
    pricingPath: "/pricing",
  },
  export_format_quota_blocked: {
    surface: "modal",
    source: "export_format_quota_blocked",
    billingPath: "/dashboard/billing",
    pricingPath: "/pricing",
  },
  material_upload_quota_blocked: {
    surface: "modal",
    source: "material_upload_quota_blocked",
    billingPath: "/dashboard/billing",
    pricingPath: "/pricing",
  },
  material_decompose_quota_blocked: {
    surface: "modal",
    source: "material_decompose_quota_blocked",
    billingPath: "/dashboard/billing",
    pricingPath: "/pricing",
  },
  skill_create_quota_blocked: {
    surface: "modal",
    source: "skill_create_quota_blocked",
    billingPath: "/dashboard/billing",
    pricingPath: "/pricing",
  },
  settings_subscription_upgrade: {
    surface: "page",
    source: "settings_subscription_upgrade",
    billingPath: "/dashboard/billing",
    pricingPath: "/pricing",
  },
  billing_header_upgrade: {
    surface: "page",
    source: "billing_header_upgrade",
    billingPath: "/dashboard/billing",
    pricingPath: "/pricing",
  },
};

export function getUpgradePromptDefinition(scenario: UpgradePromptScenario): UpgradePromptDefinition {
  return UPGRADE_PROMPT_DEFINITIONS[scenario];
}

export function buildUpgradeUrl(path: string, source: string): string {
  const normalizedSource = source.trim();
  if (!normalizedSource) {
    return path;
  }

  const hashIndex = path.indexOf("#");
  const pathWithoutHash = hashIndex >= 0 ? path.slice(0, hashIndex) : path;
  const hashFragment = hashIndex >= 0 ? path.slice(hashIndex) : "";

  const queryIndex = pathWithoutHash.indexOf("?");
  const pathname = queryIndex >= 0 ? pathWithoutHash.slice(0, queryIndex) : pathWithoutHash;
  const queryString = queryIndex >= 0 ? pathWithoutHash.slice(queryIndex + 1) : "";

  const searchParams = new URLSearchParams(queryString);
  searchParams.set("source", normalizedSource);

  const serializedQuery = searchParams.toString();
  const nextPath = serializedQuery ? `${pathname}?${serializedQuery}` : pathname;
  return `${nextPath}${hashFragment}`;
}

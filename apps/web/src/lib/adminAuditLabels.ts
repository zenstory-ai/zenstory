/**
 * Labels for the resource types and actions the backend writes to the admin
 * audit log (services/admin_audit_service.log_action callers under api/admin).
 *
 * Unknown values from newer backends fall back to a readable form of the raw
 * string ("sync_payment_order" -> "Sync payment order") instead of a key.
 */
import type { TFunction } from "i18next";

/** Resource type -> actions logged for it, in display order. */
export const AUDIT_ACTIONS_BY_RESOURCE: Readonly<Record<string, readonly string[]>> = {
  user: ["update_user", "delete_user"],
  subscription: ["update_subscription"],
  payment_order: ["sync_payment_order"],
  plan: ["update_plan"],
  code: ["create_code", "create_codes_batch", "update_code"],
  points: ["adjust_points"],
  invite_code: ["create_invite_code"],
  skill: ["approve_skill", "reject_skill", "unpublish_skill"],
  inspiration: [
    "create_inspiration",
    "update_inspiration",
    "approve_inspiration",
    "reject_inspiration",
    "delete_inspiration",
  ],
  feedback: ["update_feedback_status"],
  system_prompt: ["create_prompt", "update_prompt", "delete_prompt"],
};

export const AUDIT_RESOURCE_TYPES = Object.keys(AUDIT_ACTIONS_BY_RESOURCE);

export const AUDIT_ACTIONS = Object.values(AUDIT_ACTIONS_BY_RESOURCE).flat();

/** "sync_payment_order" -> "Sync payment order". */
export function humanizeIdentifier(value: string): string {
  const words = value.replace(/[_-]+/g, " ").trim();
  if (!words) return value;
  return words.charAt(0).toUpperCase() + words.slice(1);
}

type Translate = TFunction | ((key: string, options?: Record<string, unknown>) => string);

export function auditResourceLabel(t: Translate, resourceType: string): string {
  return t(`auditLogs.resourceLabels.${resourceType}`, { defaultValue: humanizeIdentifier(resourceType) });
}

export function auditActionLabel(t: Translate, action: string): string {
  return t(`auditLogs.actionLabels.${action}`, { defaultValue: humanizeIdentifier(action) });
}

/** Badge colour by what the action does. */
export function auditActionTone(action: string): "create" | "remove" | "change" | "neutral" {
  if (action.startsWith("create")) return "create";
  if (/^(delete|reject|unpublish)/.test(action)) return "remove";
  if (/^(update|approve|adjust|sync)/.test(action)) return "change";
  return "neutral";
}

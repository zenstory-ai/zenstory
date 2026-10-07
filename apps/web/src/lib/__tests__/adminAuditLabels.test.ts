import { describe, expect, it } from "vitest";
import en from "../../../public/locales/en/admin.json";
import zh from "../../../public/locales/zh/admin.json";
import {
  AUDIT_ACTIONS,
  AUDIT_RESOURCE_TYPES,
  auditActionLabel,
  auditActionTone,
  auditResourceLabel,
  humanizeIdentifier,
} from "../adminAuditLabels";

describe("adminAuditLabels", () => {
  it.each([["zh", zh], ["en", en]])("has a %s label for every logged resource type and action", (_lang, locale) => {
    const labels = locale.auditLogs as unknown as {
      resourceLabels: Record<string, string>;
      actionLabels: Record<string, string>;
    };
    for (const type of AUDIT_RESOURCE_TYPES) expect(labels.resourceLabels[type], type).toBeTruthy();
    for (const action of AUDIT_ACTIONS) expect(labels.actionLabels[action], action).toBeTruthy();
  });

  it("falls back to a readable form for values it does not know", () => {
    const t = (_key: string, options?: Record<string, unknown>) => String(options?.defaultValue);
    expect(auditActionLabel(t, "refund_payment_order")).toBe("Refund payment order");
    expect(auditResourceLabel(t, "api-key")).toBe("Api key");
    expect(humanizeIdentifier("__")).toBe("__");
  });

  it("colours actions by effect", () => {
    expect(auditActionTone("create_codes_batch")).toBe("create");
    expect(auditActionTone("unpublish_skill")).toBe("remove");
    expect(auditActionTone("sync_payment_order")).toBe("change");
    expect(auditActionTone("login")).toBe("neutral");
  });
});

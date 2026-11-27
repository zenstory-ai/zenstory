/**
 * Editor safety banners: deleted-file draft recovery, reload draft recovery,
 * and server-sync retry notice.
 *
 * @module components/editor/EditorBanners
 */

import React from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Download } from "lucide-react";
import { Button } from "../ui/Button";
import type {
  DraftRecoveryState,
  RemovedDraftEntry,
  ServerSyncIssueState,
} from "./editorFileHelpers";

export interface RemovedDraftsBannerProps {
  removedDrafts: RemovedDraftEntry[];
  currentUserId: string | null;
  currentProjectId: string | null;
}

export const RemovedDraftsBanner: React.FC<RemovedDraftsBannerProps> = ({
  removedDrafts,
  currentUserId,
  currentProjectId,
}) => {
  const { t } = useTranslation(["editor"]);

  const entries = removedDrafts
    .filter(
      ({ snapshot, persisted }) =>
        snapshot.userId === currentUserId &&
        (snapshot.projectId === currentProjectId || !persisted),
    )
    .flatMap(({ snapshot, persisted }) => [
      { snapshot, persisted },
      ...(snapshot.recoveryDraft
        ? [{ snapshot: { ...snapshot, ...snapshot.recoveryDraft, recoveryDraft: undefined }, persisted }]
        : []),
    ]);

  if (entries.length === 0) return null;

  return (
    <>
      {entries.map(({ snapshot, persisted }, index) => (
        <details
          key={`${snapshot.projectId}:${snapshot.fileId}:${index}`}
          open
          className="group min-w-0 shrink-0 border-b border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-6 py-3"
        >
          <summary className="flex min-h-11 min-w-0 cursor-pointer list-none items-start gap-2 py-2 text-sm font-medium text-[hsl(var(--text-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] [&::-webkit-details-marker]:hidden">
            <ChevronDown
              size={16}
              aria-hidden="true"
              className="mt-0.5 shrink-0 -rotate-90 text-[hsl(var(--text-secondary))] motion-chevron group-open:rotate-0"
            />
            <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
              {t("editor:deletedFile.recovery", { title: snapshot.title })}
            </span>
          </summary>
          <div className="mt-1 space-y-3">
            <p
              role={persisted ? "status" : "alert"}
              className={`text-sm leading-relaxed ${
                persisted ? "text-[hsl(var(--text-secondary))]" : "text-[hsl(var(--warning-text))]"
              }`}
            >
              {t(persisted ? "editor:deletedFile.saved" : "editor:deletedFile.storageFailed")}
            </p>
            <button
              type="button"
              className="btn-secondary inline-flex min-h-11 items-center gap-2"
              onClick={() => {
                const url = URL.createObjectURL(
                  new Blob([`${snapshot.title}\n\n${snapshot.content}`], {
                    type: "text/plain;charset=utf-8",
                  }),
                );
                const anchor = document.createElement("a");
                anchor.href = url;
                anchor.download = `${snapshot.title || "draft"}.txt`;
                document.body.appendChild(anchor);
                anchor.click();
                anchor.remove();
                setTimeout(() => URL.revokeObjectURL(url), 0);
              }}
            >
              <Download size={16} aria-hidden="true" />
              {t("editor:deletedFile.download")}
            </button>
            <textarea
              aria-label={t("editor:deletedFile.draft")}
              readOnly
              value={snapshot.content}
              className="block h-24 w-full min-w-0 resize-y rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] p-3 text-sm leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] sm:h-28"
            />
          </div>
        </details>
      ))}
    </>
  );
};

export interface DraftRecoveryBannerProps {
  draftRecovery: DraftRecoveryState;
  isMobile: boolean;
  onApply: () => void;
  onDiscard: () => void;
}

export const DraftRecoveryBanner: React.FC<DraftRecoveryBannerProps> = ({
  draftRecovery,
  isMobile,
  onApply,
  onDiscard,
}) => {
  const { t } = useTranslation(["editor"]);
  const isUnappliedConflict = draftRecovery.kind === "conflict" && !draftRecovery.applied;

  return (
    <div
      role={isUnappliedConflict ? "alert" : "status"}
      className="border-b border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning)/0.08)] px-4 py-3 text-sm text-[hsl(var(--text-primary))]"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p>
          {t(isUnappliedConflict ? "editor:draftRecovery.conflict" : "editor:draftRecovery.restored")}
        </p>
        <div className="flex flex-wrap gap-2">
          {isUnappliedConflict && (
            <Button type="button" size={isMobile ? "touch" : "sm"} onClick={onApply}>
              {t("editor:draftRecovery.restore")}
            </Button>
          )}
          <Button
            type="button"
            variant="secondary"
            size={isMobile ? "touch" : "sm"}
            onClick={onDiscard}
          >
            {t("editor:draftRecovery.discard")}
          </Button>
        </div>
      </div>
      {isUnappliedConflict && (
        <details className="mt-2">
          <summary className="inline-flex min-h-11 cursor-pointer items-center text-xs font-medium">
            {t("editor:draftRecovery.compare")}
          </summary>
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            <div>
              <p className="mb-1 text-xs font-medium">{t("editor:draftRecovery.serverVersion")}</p>
              <p className="mb-1 text-xs text-[hsl(var(--text-secondary))]">
                {draftRecovery.serverTitle}
              </p>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-[hsl(var(--bg-primary))] p-2 text-xs">
                {draftRecovery.serverContent}
              </pre>
            </div>
            <div>
              <p className="mb-1 text-xs font-medium">{t("editor:draftRecovery.localVersion")}</p>
              <p className="mb-1 text-xs text-[hsl(var(--text-secondary))]">
                {draftRecovery.snapshot.title}
              </p>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-[hsl(var(--bg-primary))] p-2 text-xs">
                {draftRecovery.snapshot.content}
              </pre>
            </div>
          </div>
        </details>
      )}
    </div>
  );
};

export interface ServerSyncIssueBannerProps {
  serverSyncIssue: ServerSyncIssueState;
  isMobile: boolean;
  onRetry: () => void;
}

export const ServerSyncIssueBanner: React.FC<ServerSyncIssueBannerProps> = ({
  serverSyncIssue,
  isMobile,
  onRetry,
}) => {
  const { t } = useTranslation(["editor"]);
  return (
    <div
      role="alert"
      className="border-b border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning)/0.08)] px-4 py-3 text-sm text-[hsl(var(--text-primary))]"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p>{t("editor:serverSyncFailed.message")}</p>
        <Button
          type="button"
          size={isMobile ? "touch" : "sm"}
          onClick={onRetry}
          disabled={serverSyncIssue.retrying}
        >
          {t(
            serverSyncIssue.retrying
              ? "editor:serverSyncFailed.retrying"
              : "editor:serverSyncFailed.retry",
          )}
        </Button>
      </div>
    </div>
  );
};

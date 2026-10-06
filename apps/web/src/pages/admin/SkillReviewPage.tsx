import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { LazyMarkdown } from "../../components/LazyMarkdown";
import { Check, X, Zap, ChevronDown, ChevronUp, AlertTriangle, RefreshCw, EyeOff, FileText } from "lucide-react";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { adminApi, type PendingSkill, type SkillReviewResource, type SkillReviewStatus } from "../../lib/adminApi";
import { logger } from "../../lib/logger";
import { formatAdminDate, formatAdminDateTime } from "../../lib/dateUtils";

export default function SkillReviewPage() {
  const { t } = useTranslation(["admin", "common"]);
  const [skills, setSkills] = useState<PendingSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [unpublishingId, setUnpublishingId] = useState<string | null>(null);
  const [unpublishReason, setUnpublishReason] = useState("");
  const [statusFilter, setStatusFilter] = useState<SkillReviewStatus>("pending");
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const loadPendingSkills = useCallback(async (showLoadingIndicator = true) => {
    setLoadError(null);
    try {
      if (showLoadingIndicator) {
        setLoading(true);
      }
      const data = await adminApi.getPendingSkills(statusFilter);
      setSkills(Array.isArray(data) ? data : []);
    } catch (error) {
      logger.error("Failed to load pending skills:", error);
      const fallbackError = t("admin:dashboard.loadError", "加载失败，请重试");
      setLoadError(error instanceof Error && error.message ? error.message : fallbackError);
    } finally {
      if (showLoadingIndicator) {
        setLoading(false);
      }
    }
  }, [statusFilter, t]);

  useEffect(() => {
    void loadPendingSkills();
  }, [loadPendingSkills]);

  const handleApprove = async (skillId: string) => {
    try {
      setProcessingId(skillId);
      setDecisionError(null);
      await adminApi.approveSkill(skillId);
      setSkills((prev) => prev.filter((s) => s.id !== skillId));
      // Refresh without loading indicator to avoid flashing
      await loadPendingSkills(false);
    } catch (error) {
      logger.error("Failed to approve skill:", error);
      setDecisionError(error instanceof Error && error.message
        ? error.message
        : t("admin:skills.decisionFailed", "操作失败，请重试"));
    } finally {
      setProcessingId(null);
      setApprovingId(null);
    }
  };

  const handleReject = async (skillId: string) => {
    try {
      setProcessingId(skillId);
      setDecisionError(null);
      await adminApi.rejectSkill(skillId, rejectReason || undefined);
      setSkills((prev) => prev.filter((s) => s.id !== skillId));
      setRejectingId(null);
      setRejectReason("");
      // Refresh without loading indicator to avoid flashing
      await loadPendingSkills(false);
    } catch (error) {
      logger.error("Failed to reject skill:", error);
      setDecisionError(error instanceof Error && error.message
        ? error.message
        : t("admin:skills.decisionFailed", "操作失败，请重试"));
    } finally {
      setProcessingId(null);
    }
  };

  const handleUnpublish = async (skillId: string) => {
    try {
      setProcessingId(skillId);
      setDecisionError(null);
      await adminApi.unpublishSkill(skillId, unpublishReason || undefined);
      setSkills((prev) => prev.filter((s) => s.id !== skillId));
      setUnpublishingId(null);
      setUnpublishReason("");
      await loadPendingSkills(false);
    } catch (error) {
      logger.error("Failed to unpublish skill:", error);
      setDecisionError(error instanceof Error && error.message
        ? error.message
        : t("admin:skills.decisionFailed", "操作失败，请重试"));
    } finally {
      setProcessingId(null);
    }
  };

  const hasBlockingError = Boolean(loadError) && skills.length === 0;
  const displayError = loadError ?? t("admin:dashboard.loadError", "加载失败，请重试");
  const approvingSkill = skills.find((skill) => skill.id === approvingId);

  return (
    <div className="admin-page">
      <div>
        <h1 className="admin-page-title">
          {t("admin:skills.title", "技能审核")}
        </h1>
        <p className="admin-page-subtitle">
          {t("admin:skills.description", "检查社区提交的技能，决定是否公开")}
        </p>
      </div>

      <AdminSelect
        value={statusFilter}
        onChange={(event) => setStatusFilter(event.target.value as SkillReviewStatus)}
        aria-label={t("admin:skills.statusFilter", "审核状态")}
      >
        <option value="pending">{t("admin:skills.pending", "待审核")}</option>
        <option value="approved">{t("admin:skills.approved", "已批准")}</option>
        <option value="rejected">{t("admin:skills.rejected", "已拒绝")}</option>
        <option value="unpublished">{t("admin:skills.unpublished", "已下架")}</option>
      </AdminSelect>

      {decisionError && (
        <div role="alert" className="rounded-lg border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] p-4 text-sm text-[hsl(var(--error))]">
          {decisionError}
        </div>
      )}

      {loadError && skills.length > 0 && (
        <div className="mb-4 rounded-lg border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] p-4 text-sm">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-[hsl(var(--error))] mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="font-medium text-[hsl(var(--error))]">
                {t("admin:dashboard.loadError", "加载失败，请重试")}
              </p>
              <p className="mt-1 text-[hsl(var(--text-secondary))] break-words">{loadError}</p>
            </div>
            <button
              onClick={() => void loadPendingSkills(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[hsl(var(--separator-color))] px-3 py-1.5 text-xs text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              {t("common:retry")}
            </button>
          </div>
        </div>
      )}

      <AdminPageState
        isLoading={loading}
        isError={hasBlockingError}
        isEmpty={skills.length === 0}
        loadingText={t("common:loading")}
        errorText={displayError}
        emptyText={statusFilter === "pending"
          ? t("admin:skills.noPending", "没有待审核的技能")
          : t("admin:skills.noHistory", "没有符合条件的审核记录")}
        retryText={t("common:retry")}
        onRetry={() => {
          void loadPendingSkills(true);
        }}
      >
        <div className="space-y-4">
          {skills.map((skill) => (
            <SkillReviewCard
              key={skill.id}
              skill={skill}
              onApprove={() => setApprovingId(skill.id)}
              onReject={() => setRejectingId(skill.id)}
              onUnpublish={() => setUnpublishingId(skill.id)}
              processing={processingId === skill.id}
            />
          ))}
        </div>
      </AdminPageState>

      {/* Reject Modal */}
      {rejectingId && (
        <ReasonModal
          title={t("admin:skills.rejectTitle", "拒绝技能")}
          label={t("admin:skills.rejectReason", "拒绝原因（可选）")}
          placeholder={t("admin:skills.rejectPlaceholder", "填写拒绝原因")}
          confirmLabel={t("admin:skills.confirmReject", "确认拒绝")}
          onConfirm={() => handleReject(rejectingId)}
          onCancel={() => {
            setRejectingId(null);
            setRejectReason("");
          }}
          reason={rejectReason}
          setReason={setRejectReason}
          processing={processingId === rejectingId}
        />
      )}

      {/* Unpublish Modal */}
      {unpublishingId && (
        <ReasonModal
          title={t("admin:skills.unpublishTitle", "下架技能")}
          description={t(
            "admin:skills.unpublishConfirmation",
            "下架后该技能会立即从公共库移除，已添加它的用户也无法再使用。",
          )}
          label={t("admin:skills.unpublishReason", "下架原因（可选）")}
          placeholder={t("admin:skills.unpublishPlaceholder", "填写下架原因")}
          confirmLabel={t("admin:skills.confirmUnpublish", "确认下架")}
          onConfirm={() => handleUnpublish(unpublishingId)}
          onCancel={() => {
            setUnpublishingId(null);
            setUnpublishReason("");
          }}
          reason={unpublishReason}
          setReason={setUnpublishReason}
          processing={processingId === unpublishingId}
        />
      )}

      <ConfirmDialog
        open={Boolean(approvingId)}
        onClose={() => setApprovingId(null)}
        onConfirm={() => approvingId ? handleApprove(approvingId) : undefined}
        title={t("admin:skills.approveTitle", "批准技能")}
        message={`${approvingSkill?.name ?? ""}: ${t(
          "admin:skills.approveConfirmation",
          "批准后，所有用户都能使用该技能。",
        )}`}
        confirmLabel={t("admin:skills.confirmApprove", "批准并公开")}
        cancelLabel={t("common:cancel")}
        loading={Boolean(approvingId && processingId === approvingId)}
      />
    </div>
  );
}

function SkillReviewCard({
  skill,
  onApprove,
  onReject,
  onUnpublish,
  processing,
}: {
  skill: PendingSkill;
  onApprove: () => void;
  onReject: () => void;
  onUnpublish: () => void;
  processing: boolean;
}) {
  const { t } = useTranslation(["admin"]);
  const [expanded, setExpanded] = useState(false);
  const tags = skill.tags ?? [];
  const resourceCount = skill.resource_count ?? 0;

  return (
    <div className="bg-[hsl(var(--bg-secondary))] rounded-xl border border-[hsl(var(--border-color))] p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-10 h-10 rounded-lg bg-[hsl(var(--bg-tertiary))] flex items-center justify-center">
              <Zap className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
            </div>
            <div>
              <h3 className="font-semibold text-[hsl(var(--text-primary))]">
                {skill.name}
              </h3>
              <p className="text-xs text-[hsl(var(--text-tertiary))]">
                {t("admin:skills.submittedBy", "提交者")}: {skill.author_name || "Unknown"}
              </p>
            </div>
          </div>
          {skill.description && (
            <p className="text-sm text-[hsl(var(--text-secondary))] mb-2">
              {skill.description}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
              {skill.category}
            </span>
            {resourceCount > 0 && (
              <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-[hsl(var(--warning)/0.12)] text-[hsl(var(--warning))]">
                <FileText className="w-3 h-3" />
                {t("admin:skills.resourceCount", { count: resourceCount, defaultValue: "{{count}} 个资源文件" })}
              </span>
            )}
            <span className="text-xs text-[hsl(var(--text-tertiary))]">
              {formatAdminDate(skill.created_at)}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {skill.status === "pending" && (
            <>
              <button
                onClick={onApprove}
                disabled={processing}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-[hsl(var(--success)/0.1)] text-[hsl(var(--success))] hover:bg-[hsl(var(--success)/0.2)] transition-colors disabled:opacity-50"
              >
                {processing ? (
                  <div className="w-5 h-5 animate-spin rounded-full border-2 border-[hsl(var(--success))] border-t-transparent" />
                ) : (
                  <Check className="w-5 h-5" />
                )}
                {t("admin:skills.approve", "批准")}
              </button>
              <button
                onClick={onReject}
                disabled={processing}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-[hsl(var(--error)/0.1)] text-[hsl(var(--error))] hover:bg-[hsl(var(--error)/0.2)] transition-colors disabled:opacity-50"
              >
                <X className="w-5 h-5" />
                {t("admin:skills.reject", "拒绝")}
              </button>
            </>
          )}
          {skill.status === "approved" && (
            <button
              onClick={onUnpublish}
              disabled={processing}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-[hsl(var(--error)/0.1)] text-[hsl(var(--error))] hover:bg-[hsl(var(--error)/0.2)] transition-colors disabled:opacity-50"
            >
              <EyeOff className="w-5 h-5" />
              {t("admin:skills.unpublish", "下架")}
            </button>
          )}
          <button
            onClick={() => setExpanded(!expanded)}
            className="p-2 rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
          >
            {expanded ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
          </button>
        </div>
      </div>

      {skill.status !== "pending" && (
        <div className="mt-3 rounded-lg bg-[hsl(var(--bg-tertiary))] p-3 text-xs text-[hsl(var(--text-secondary))]">
          <p>{t("admin:skills.status", "状态")}: {skill.status}</p>
          <p>{t("admin:skills.reviewedBy", "审核人")}: {skill.reviewer_name || skill.reviewed_by || "-"}</p>
          <p>{t("admin:skills.reviewedAt", "审核时间")}: {formatAdminDateTime(skill.reviewed_at)}</p>
          {skill.rejection_reason && (
            <p>
              {skill.status === "unpublished"
                ? t("admin:skills.unpublishedReason", "下架原因")
                : t("admin:skills.rejectedReason", "拒绝原因")}: {skill.rejection_reason}
            </p>
          )}
        </div>
      )}

      {expanded && (
        <SkillReviewMaterials skill={skill} tags={tags} resourceCount={resourceCount} />
      )}
    </div>
  );
}

const RAW_TEXT_CLASS =
  "bg-[hsl(var(--bg-tertiary))] rounded-lg p-4 text-xs font-mono whitespace-pre-wrap break-words max-h-80 overflow-y-auto text-[hsl(var(--text-primary))]";

/**
 * Everything that reaches other users' agents once the skill is approved:
 * the raw instructions (hidden markdown such as link reference definitions and
 * HTML comments included), trigger tags, skill_metadata and every resource file.
 */
function SkillReviewMaterials({
  skill,
  tags,
  resourceCount,
}: {
  skill: PendingSkill;
  tags: string[];
  resourceCount: number;
}) {
  const { t } = useTranslation(["admin"]);
  const [view, setView] = useState<"raw" | "rendered">("raw");
  const [resources, setResources] = useState<SkillReviewResource[] | null>(null);
  const [resourcesError, setResourcesError] = useState<string | null>(null);
  const metadata = skill.skill_metadata ?? {};
  const hasMetadata = Object.keys(metadata).length > 0;

  useEffect(() => {
    if (resourceCount === 0) return;
    let cancelled = false;
    adminApi.getSkillReviewResources(skill.id)
      .then((items) => {
        if (!cancelled) setResources(items);
      })
      .catch((error: unknown) => {
        logger.error("Failed to load skill review resources:", error);
        if (!cancelled) {
          setResourcesError(error instanceof Error && error.message
            ? error.message
            : t("admin:skills.resourcesLoadFailed", "资源文件加载失败，请重试"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [skill.id, resourceCount, t]);

  return (
    <div className="mt-4 pt-4 border-t border-[hsl(var(--border-color))] space-y-4">
      <p className="text-xs text-[hsl(var(--text-tertiary))]">
        {t(
          "admin:skills.reviewHint",
          "批准后，以下内容会原样交给其他用户的 AI 使用。请在“原文”中检查：渲染预览会隐藏注释和链接定义。",
        )}
      </p>

      <div>
        <div className="flex items-center justify-between gap-2 mb-2">
          <p className="text-xs font-medium text-[hsl(var(--text-tertiary))]">
            {t("admin:skills.instructions", "指令内容")}
          </p>
          <div className="inline-flex rounded-lg border border-[hsl(var(--border-color))] overflow-hidden text-xs">
            {(["raw", "rendered"] as const).map((mode) => (
              <button
                key={mode}
                onClick={() => setView(mode)}
                aria-pressed={view === mode}
                className={`px-2.5 py-1 transition-colors ${
                  view === mode
                    ? "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))]"
                    : "text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]"
                }`}
              >
                {mode === "raw"
                  ? t("admin:skills.rawView", "原文")
                  : t("admin:skills.renderedView", "渲染预览")}
              </button>
            ))}
          </div>
        </div>
        {view === "raw" ? (
          <pre className={RAW_TEXT_CLASS} data-testid="skill-review-raw-instructions">{skill.instructions}</pre>
        ) : (
          <div className="markdown-content bg-[hsl(var(--bg-tertiary))] rounded-lg p-4 text-sm max-h-64 overflow-y-auto">
            <LazyMarkdown>{skill.instructions}</LazyMarkdown>
          </div>
        )}
      </div>

      <div>
        <p className="text-xs font-medium text-[hsl(var(--text-tertiary))] mb-2">
          {t("admin:skills.tags", "触发词")}
        </p>
        {tags.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {tags.map((tag, index) => (
              <span
                key={`${tag}-${index}`}
                className="text-xs px-2 py-0.5 rounded-full bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] break-all"
              >
                {tag}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-xs text-[hsl(var(--text-tertiary))]">{t("admin:skills.none", "无")}</p>
        )}
      </div>

      {hasMetadata && (
        <div>
          <p className="text-xs font-medium text-[hsl(var(--text-tertiary))] mb-2">
            {t("admin:skills.metadata", "元数据")}
          </p>
          <pre className={RAW_TEXT_CLASS}>{JSON.stringify(metadata, null, 2)}</pre>
        </div>
      )}

      <div>
        <p className="text-xs font-medium text-[hsl(var(--text-tertiary))] mb-2">
          {t("admin:skills.resources", "资源文件")}
        </p>
        {resourceCount === 0 ? (
          <p className="text-xs text-[hsl(var(--text-tertiary))]">{t("admin:skills.none", "无")}</p>
        ) : resourcesError ? (
          <p role="alert" className="text-xs text-[hsl(var(--error))]">{resourcesError}</p>
        ) : resources === null ? (
          <p className="text-xs text-[hsl(var(--text-tertiary))]">
            {t("admin:skills.loadingResources", "正在加载资源文件...")}
          </p>
        ) : (
          <div className="space-y-2">
            {resources.map((resource) => (
              <details
                key={resource.path}
                className="rounded-lg border border-[hsl(var(--border-color))]"
              >
                <summary className="cursor-pointer px-3 py-2 text-xs text-[hsl(var(--text-primary))] font-mono break-all">
                  {resource.path}
                  <span className="ml-2 text-[hsl(var(--text-tertiary))]">{resource.size} B</span>
                </summary>
                <pre className={`${RAW_TEXT_CLASS} rounded-t-none`}>{resource.content}</pre>
              </details>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ReasonModal({
  title,
  description,
  label,
  placeholder,
  confirmLabel,
  onConfirm,
  onCancel,
  reason,
  setReason,
  processing,
}: {
  title: string;
  description?: string;
  label: string;
  placeholder: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  reason: string;
  setReason: (r: string) => void;
  processing: boolean;
}) {
  const { t } = useTranslation(["admin", "common"]);

  return (
    <div className="modal-overlay flex items-center justify-center p-4" onClick={onCancel}>
      <div className="modal w-full max-w-md animate-scale-in" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))] mb-4">
          {title}
        </h2>
        {description && (
          <p className="text-sm text-[hsl(var(--text-secondary))] mb-4">{description}</p>
        )}
        <div className="mb-4">
          <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
            {label}
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="input w-full min-h-[100px] resize-y"
            placeholder={placeholder}
            maxLength={500}
          />
        </div>
        <div className="flex gap-3">
          <button onClick={onCancel} className="btn-ghost flex-1 h-11">
            {t("common:cancel")}
          </button>
          <button
            onClick={onConfirm}
            disabled={processing}
            className="btn-danger flex-1 h-11 flex items-center justify-center gap-2"
          >
            {processing && <div className="w-4 h-4 animate-spin rounded-full border-2 border-white border-t-transparent" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

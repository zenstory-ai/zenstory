import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, ImagePlus, Trash2 } from "lucide-react";

import Modal from "../ui/Modal";
import { debugContext } from "../../lib/debugContext";
import { feedbackApi, type FeedbackSourcePage } from "../../lib/feedbackApi";
import { handleApiError } from "../../lib/errorHandler";
import { toast } from "../../lib/toast";

interface FeedbackDialogProps {
  open: boolean;
  onClose: () => void;
  sourcePage: FeedbackSourcePage;
  sourceRoute?: string;
}

const MAX_ISSUE_LENGTH = 2000;
const MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024;
const ACCEPTED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];

export function FeedbackDialog({
  open,
  onClose,
  sourcePage,
  sourceRoute,
}: FeedbackDialogProps) {
  const { t } = useTranslation("common");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [issueText, setIssueText] = useState("");
  const [screenshotFile, setScreenshotFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  const issueTextLength = issueText.trim().length;
  const issueCharsLeft = MAX_ISSUE_LENGTH - issueTextLength;
  const canSubmit = issueTextLength > 0 && issueCharsLeft >= 0 && !isSubmitting;

  const screenshotMeta = useMemo(() => {
    if (!screenshotFile) return null;
    return `${(screenshotFile.size / 1024 / 1024).toFixed(2)}MB`;
  }, [screenshotFile]);

  const debugSnapshot = useMemo(() => {
    if (!open) return null;
    const snapshot = debugContext.get();
    if (!snapshot) return null;
    if (!snapshot.trace_id && !snapshot.request_id && !snapshot.agent_run_id) return null;
    return snapshot;
  }, [open]);

  useEffect(() => {
    return () => {
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
    };
  }, [previewUrl]);

  const resetForm = () => {
    setIssueText("");
    setScreenshotFile(null);
    setValidationError(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
    }
    setPreviewUrl(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const closeDialog = (force = false) => {
    if (isSubmitting && !force) return;
    resetForm();
    onClose();
  };

  const handleSelectFile = (file: File) => {
    if (!ACCEPTED_IMAGE_TYPES.includes(file.type)) {
      setValidationError(t("feedback.invalidImageType", "请上传 PNG/JPG/WEBP 图片"));
      return;
    }
    if (file.size > MAX_SCREENSHOT_BYTES) {
      setValidationError(t("feedback.invalidImageSize", "截图大小不能超过 5MB"));
      return;
    }

    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
    }
    setScreenshotFile(file);
    setPreviewUrl(URL.createObjectURL(file));
    setValidationError(null);
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    handleSelectFile(file);
  };

  const handleRemoveScreenshot = () => {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
    }
    setScreenshotFile(null);
    setPreviewUrl(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleSubmit = async () => {
    const normalizedIssue = issueText.trim();
    if (!normalizedIssue) {
      setValidationError(t("feedback.issueRequired", "请先填写问题描述"));
      return;
    }

    if (normalizedIssue.length > MAX_ISSUE_LENGTH) {
      setValidationError(t("feedback.issueTooLong", "问题描述不能超过 2000 字"));
      return;
    }

    setValidationError(null);
    setIsSubmitting(true);
    try {
      const submitPayload: Parameters<typeof feedbackApi.submit>[0] = {
        issueText: normalizedIssue,
        sourcePage,
        sourceRoute,
        screenshot: screenshotFile,
      };

      if (debugSnapshot) {
        submitPayload.debugContext = {
          trace_id: debugSnapshot.trace_id,
          request_id: debugSnapshot.request_id,
          agent_run_id: debugSnapshot.agent_run_id,
          project_id: debugSnapshot.project_id,
          agent_session_id: debugSnapshot.agent_session_id ?? null,
        };
      }

      await feedbackApi.submit(submitPayload);
      toast.success(t("feedback.submitSuccess", "感谢反馈，我们已收到。"));
      closeDialog(true);
    } catch (error) {
      toast.error(handleApiError(error));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={() => closeDialog()}
      title={t("feedback.title", "反馈问题")}
      description={t(
        "feedback.description",
        "遇到问题或有改进建议？告诉我们。"
      )}
      size="lg"
      footer={
        <>
          <button
            type="button"
            className="btn-ghost h-11 px-5"
            onClick={() => closeDialog()}
            disabled={isSubmitting}
          >
            {t("cancel", "取消")}
          </button>
          <button
            type="button"
            className="btn-primary h-11 px-5"
            onClick={() => {
              void handleSubmit();
            }}
            disabled={!canSubmit}
          >
            {isSubmitting
              ? t("feedback.submitting", "提交中...")
              : t("feedback.submit", "提交反馈")}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label
            htmlFor="feedback-issue-text"
            className="mb-2 block text-sm font-medium text-[hsl(var(--text-primary))]"
          >
            {t("feedback.issueLabel", "问题描述")}
            <span className="ml-1 text-[hsl(var(--error))]">*</span>
          </label>
          <textarea
            id="feedback-issue-text"
            data-modal-autofocus
            className="input min-h-[140px] resize-y"
            placeholder={t(
              "feedback.issuePlaceholder",
              "请描述你遇到的问题（发生了什么、期望结果、复现步骤等）"
            )}
            value={issueText}
            maxLength={MAX_ISSUE_LENGTH}
            disabled={isSubmitting}
            onChange={(event) => {
              setIssueText(event.target.value);
              if (validationError) {
                setValidationError(null);
              }
            }}
          />
          <div className="mt-2 flex items-center justify-between text-xs text-[hsl(var(--text-secondary))]">
            <span>{t("feedback.issueHint", "最多 {{max}} 字", { max: MAX_ISSUE_LENGTH })}</span>
            <span
              className={
                issueCharsLeft < 0 ? "text-[hsl(var(--error))]" : "text-[hsl(var(--text-secondary))]"
              }
            >
              {issueTextLength}/{MAX_ISSUE_LENGTH}
            </span>
          </div>
        </div>

        <div>
          <p className="mb-2 text-sm font-medium text-[hsl(var(--text-primary))]">
            {t("feedback.screenshotLabel", "截图（可选）")}
          </p>
          <p className="mb-3 text-xs text-[hsl(var(--text-secondary))]">
            {t("feedback.screenshotHint", "支持 PNG / JPG / WEBP，最大 5MB")}
          </p>

          <input
            ref={fileInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={handleFileChange}
            disabled={isSubmitting}
          />

          {!screenshotFile && (
            <button
              type="button"
              className="w-full rounded-xl border border-dashed border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary)/0.45)] px-4 py-6 text-sm text-[hsl(var(--text-secondary))] transition-colors hover:border-[hsl(var(--accent-primary)/0.45)] hover:text-[hsl(var(--text-primary))]"
              onClick={() => fileInputRef.current?.click()}
              disabled={isSubmitting}
            >
              <span className="flex items-center justify-center gap-2">
                <ImagePlus size={16} />
                {t("feedback.uploadAction", "上传截图")}
              </span>
            </button>
          )}

          {screenshotFile && (
            <div className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary)/0.35)] p-3">
              {previewUrl && (
                <img
                  src={previewUrl}
                  alt={t("feedback.previewAlt", "反馈截图预览")}
                  className="mb-3 max-h-64 w-full rounded-lg border border-[hsl(var(--border-color))] object-contain bg-[hsl(var(--bg-primary))]"
                />
              )}
              <div className="flex items-center justify-between gap-3 text-sm">
                <div className="min-w-0">
                  <p className="truncate text-[hsl(var(--text-primary))]">{screenshotFile.name}</p>
                  {screenshotMeta && (
                    <p className="text-xs text-[hsl(var(--text-secondary))]">{screenshotMeta}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="btn-ghost h-9 px-3 text-xs"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isSubmitting}
                  >
                    {t("feedback.replaceAction", "更换")}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost h-9 px-3 text-xs text-[hsl(var(--error))] hover:text-[hsl(var(--error))]"
                    onClick={handleRemoveScreenshot}
                    disabled={isSubmitting}
                  >
                    <Trash2 size={14} />
                    {t("feedback.removeAction", "移除")}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        {validationError && (
          <div className="flex items-start gap-2 rounded-lg border border-[hsl(var(--error)/0.28)] bg-[hsl(var(--error)/0.10)] px-3 py-2 text-sm text-[hsl(var(--error))]">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <span>{validationError}</span>
          </div>
        )}
      </div>
    </Modal>
  );
}

export default FeedbackDialog;

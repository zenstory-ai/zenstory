import { useId, type ChangeEvent, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { Upload } from "../icons";
import type { MaterialTrialSelection } from "../../lib/materialUploadValidation";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";
import { MaterialTrialSelectionNote } from "./MaterialTrialNotes";

export interface MaterialUploadModalProps {
  open: boolean;
  file: File | null;
  title: string;
  uploading: boolean;
  error: string | null;
  trialAvailable: boolean;
  trialMaxChapters?: number;
  trialCharacterLimit: string;
  trialSelection: MaterialTrialSelection | null;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onClose: () => void;
  onFileChange: (e: ChangeEvent<HTMLInputElement>) => void;
  onSelectFile: () => void;
  onTitleChange: (title: string) => void;
  onUpload: () => void;
}

export function MaterialUploadModal({
  open,
  file,
  title,
  uploading,
  error,
  trialAvailable,
  trialMaxChapters,
  trialCharacterLimit,
  trialSelection,
  fileInputRef,
  onClose,
  onFileChange,
  onSelectFile,
  onTitleChange,
  onUpload,
}: MaterialUploadModalProps) {
  const { t } = useTranslation(["materials", "common"]);
  const fileSelectButtonId = useId();
  const titleInputId = useId();
  const uploadFormId = useId();

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("materials:uploadModal.title")}
      size="md"
      showCloseButton={!uploading}
      closeOnBackdropClick={!uploading}
      closeOnEscape={!uploading}
      footer={
        <>
          <Button
            type="button"
            variant="ghost"
            size="touch"
            onClick={onClose}
            disabled={uploading}
            className="flex-1"
          >
            {t("common:cancel")}
          </Button>
          <Button
            type="submit"
            form={uploadFormId}
            size="touch"
            disabled={!file || uploading}
            title={!file && !uploading ? t("materials:uploadModal.selectFileFirst", { defaultValue: "请先选择一个 .txt 文件" }) : undefined}
            isLoading={uploading}
            loadingText={t("materials:uploadModal.uploading")}
            leftIcon={<Upload className="w-4 h-4" />}
            className="flex-1"
          >
            {t("materials:uploadModal.upload")}
          </Button>
        </>
      }
    >
      <form
        id={uploadFormId}
        onSubmit={(e) => {
          e.preventDefault();
          if (file && !uploading) {
            onUpload();
          }
        }}
      >
        {trialAvailable && (
          <p data-testid="materials-trial-upload-note" className="mb-4 text-sm text-[hsl(var(--text-secondary))]">
            {t("materials:trialUploadNote", {
              defaultValue: "免费试拆每个账号仅 1 次，提交成功后即消耗机会，之后失败或删书都不会恢复。整本上传即可，只拆前 {{chapters}} 章，合计不能超过 {{characters}} 字。",
              chapters: trialMaxChapters,
              characters: trialCharacterLimit,
            })}
          </p>
        )}
        {/* File Input */}
        <div>
          <label htmlFor={fileSelectButtonId} className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-2">
            {t("materials:uploadModal.selectFile")} *
          </label>
          {/* Hidden native file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt"
            onChange={onFileChange}
            className="hidden"
          />
          {/* Custom file select button */}
          <button
            id={fileSelectButtonId}
            type="button"
            onClick={onSelectFile}
            className="w-full text-left flex items-center gap-3 p-3 border border-dashed border-[hsl(var(--border-color))] rounded-xl cursor-pointer hover:border-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]"
          >
            <div className="w-10 h-10 rounded-lg bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center shrink-0">
              <Upload className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
            </div>
            <div className="flex-1 min-w-0">
              {file ? (
                <>
                  <p className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
                    {file.name}
                  </p>
                  <p className="text-xs tabular-nums text-[hsl(var(--text-tertiary))]">
                    {(file.size / 1024).toFixed(1)} KB
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm text-[hsl(var(--text-secondary))]">
                    {t("materials:uploadModal.clickToSelect")}
                  </p>
                  <p className="text-xs text-[hsl(var(--text-tertiary))]">
                    {trialAvailable
                      ? t("materials:uploadModal.trialSupportedFormats", {
                          defaultValue: "仅支持 .txt，不超过 20MB，需要有章节标题（如“第一章”“第一回”）；前 {{chapters}} 章合计不超过 {{characters}} 字",
                          chapters: trialMaxChapters,
                          characters: trialCharacterLimit,
                        })
                      : t("materials:uploadModal.supportedFormats")}
                  </p>
                </>
              )}
            </div>
          </button>
          {file && trialAvailable && trialSelection && (
            <MaterialTrialSelectionNote selection={trialSelection} />
          )}
        </div>

        {/* Title Input */}
        <div className="mt-4">
          <label htmlFor={titleInputId} className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
            {t("materials:uploadModal.titleLabel")}
          </label>
          <input
            id={titleInputId}
            type="text"
            value={title}
            onChange={(e) => onTitleChange(e.target.value)}
            className="input"
            placeholder={t("materials:uploadModal.titlePlaceholder")}
          />
        </div>

        {/* Error Message */}
        {error && (
          <div key={error} role="alert" className="mt-4 p-3 rounded-xl bg-[hsl(var(--error)/0.1)] border border-[hsl(var(--error)/0.3)] motion-error-shake">
            <p className="text-sm text-[hsl(var(--error-text))]">{error}</p>
          </div>
        )}
      </form>
    </Modal>
  );
}

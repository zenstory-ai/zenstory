import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { logger } from "../../lib/logger";
import {
  Copy,
  Star,
  Users,
  FileText,
  Check,
  FolderOpen,
  Clapperboard,
  Globe,
  File,
} from "../icons";
import { Modal } from "../ui/Modal";
import type { InspirationDetail } from "../../types";

interface InspirationDetailDialogProps {
  inspiration: InspirationDetail | null;
  isOpen: boolean;
  onClose: () => void;
  onCopy: (id: string, projectName?: string) => Promise<void>;
  isCopying?: boolean;
}

// Icon components for different file types
const FileTypeIcons: Record<string, React.FC<{ className?: string }>> = {
  outline: FileText,
  draft: FileText,
  character: Users,
  lore: Globe,
  script: Clapperboard,
  folder: FolderOpen,
  document: File,
};

/**
 * Dialog component for viewing inspiration details and copying to workspace
 */
export function InspirationDetailDialog({
  inspiration,
  isOpen,
  onClose,
  onCopy,
  isCopying = false,
}: InspirationDetailDialogProps) {
  if (!inspiration) return null;

  return (
    <InspirationDetailDialogContent
      key={`${inspiration.id}-${isOpen ? "open" : "closed"}`}
      inspiration={inspiration}
      isOpen={isOpen}
      onClose={onClose}
      onCopy={onCopy}
      isCopying={isCopying}
    />
  );
}

function InspirationDetailDialogContent({
  inspiration,
  isOpen,
  onClose,
  onCopy,
  isCopying = false,
}: Omit<InspirationDetailDialogProps, "inspiration"> & { inspiration: InspirationDetail }) {
  const { t } = useTranslation("inspirations");
  const [projectName, setProjectName] = useState("");
  const [copySuccess, setCopySuccess] = useState(false);
  const lifetimeRef = useRef<object | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const lifetime = {};
    lifetimeRef.current = lifetime;
    return () => {
      if (lifetimeRef.current === lifetime) {
        lifetimeRef.current = null;
        if (closeTimerRef.current !== null) {
          clearTimeout(closeTimerRef.current);
          closeTimerRef.current = null;
        }
      }
    };
  }, []);

  const projectTypeLabels: Record<string, string> = {
    novel: t("projectTypes.novel"),
    short: t("projectTypes.short"),
    screenplay: t("projectTypes.screenplay"),
  };

  const handleCopy = async () => {
    const lifetime = lifetimeRef.current;
    try {
      await onCopy(inspiration.id, projectName || undefined);
      if (!lifetime || lifetimeRef.current !== lifetime) return;
      setCopySuccess(true);
      closeTimerRef.current = setTimeout(() => {
        if (lifetimeRef.current !== lifetime) return;
        closeTimerRef.current = null;
        setCopySuccess(false);
        onClose();
      }, 1500);
    } catch (error) {
      logger.error("Failed to copy inspiration:", error);
    }
  };

  const titleContent = (
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-lg sm:text-xl font-semibold text-[hsl(var(--text-primary))] truncate">
          {inspiration.name}
        </span>
        {inspiration.is_featured && (
          <Star className="w-5 h-5 text-yellow-500 shrink-0" />
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm text-[hsl(var(--text-secondary))]">
        <span className="px-2 py-0.5 bg-[hsl(var(--bg-tertiary))] rounded text-xs">
          {projectTypeLabels[inspiration.project_type] || inspiration.project_type}
        </span>
        {inspiration.source === "community" && (
          <span className="flex items-center gap-1 text-xs">
            <Users className="w-3.5 h-3.5" />
            {t("community")}
          </span>
        )}
        <span className="flex items-center gap-1 text-xs">
          <Copy className="w-3.5 h-3.5" />
          {t("copyCount", { count: inspiration.copy_count })}
        </span>
      </div>
    </div>
  );

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="xl"
      title={titleContent}
    >
      {/* Cover Image */}
      {inspiration.cover_image && (
        <div className="mb-4 rounded-lg overflow-hidden">
          <img
            src={inspiration.cover_image}
            alt={inspiration.name}
            className="w-full h-40 sm:h-48 object-cover"
          />
        </div>
      )}

      {/* Description */}
      {inspiration.description && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-[hsl(var(--text-primary))] mb-2">
            {t("description")}
          </h3>
          <p className="text-[hsl(var(--text-secondary))] text-sm leading-relaxed">
            {inspiration.description}
          </p>
        </div>
      )}

      {/* Tags */}
      {inspiration.tags.length > 0 && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-[hsl(var(--text-primary))] mb-2">
            {t("tags")}
          </h3>
          <div className="flex flex-wrap gap-2">
            {inspiration.tags.map((tag, index) => (
              <span
                key={index}
                className="text-xs px-2.5 py-1 bg-[hsl(var(--accent-primary)/0.1)]
                          text-[hsl(var(--accent-primary))] rounded-full"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* File Preview */}
      <div className="mb-4">
        <h3 className="text-sm font-medium text-[hsl(var(--text-primary))] mb-2">
          {t("fileStructure")}
        </h3>
        <div className="bg-[hsl(var(--bg-secondary))] rounded-lg p-3 sm:p-4">
          <div className="space-y-2">
            {inspiration.file_preview.map((file, index) => {
              const IconComponent = FileTypeIcons[file.file_type] || File;
              return (
                <div
                  key={index}
                  className="flex items-center gap-2 text-sm text-[hsl(var(--text-secondary))]"
                >
                  <IconComponent className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
                  <span className="flex-1 truncate">{file.title}</span>
                  {file.has_content && (
                    <FileText className="w-4 h-4 text-[hsl(var(--text-secondary))] shrink-0" />
                  )}
                </div>
              );
            })}
          </div>
          {inspiration.file_preview.length === 0 && (
            <p className="text-sm text-[hsl(var(--text-secondary))] italic">
              {t("noFiles")}
            </p>
          )}
        </div>
      </div>

      {/* Custom Project Name */}
      <div className="mb-4">
        <h3 className="text-sm font-medium text-[hsl(var(--text-primary))] mb-2">
          {t("projectName")}
        </h3>
        <input
          type="text"
          value={projectName}
          onChange={(e) => setProjectName(e.target.value)}
          placeholder={inspiration.name}
          className="w-full px-4 py-2 border border-[hsl(var(--border-color))]
                    rounded-lg bg-[hsl(var(--bg-card))]
                    text-[hsl(var(--text-primary))]
                    focus:ring-2 focus:ring-[hsl(var(--accent-primary))] focus:border-transparent
                    placeholder:text-[hsl(var(--text-secondary))]"
        />
        <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
          {t("projectNameHint")}
        </p>
      </div>

      {/* Footer buttons */}
      <div className="flex items-center justify-end gap-3 pt-4 border-t border-[hsl(var(--border-color))]">
        <button
          onClick={onClose}
          className="px-4 py-2 text-[hsl(var(--text-secondary))]
                    hover:bg-[hsl(var(--bg-tertiary))] rounded-lg
                    transition-colors text-sm"
        >
          {t("cancel")}
        </button>
        <button
          onClick={handleCopy}
          disabled={isCopying || copySuccess}
          className="px-4 py-2 bg-[hsl(var(--accent-primary))] text-white rounded-lg
                    hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed
                    transition-colors flex items-center gap-2 text-sm"
        >
          {copySuccess ? (
            <>
              <Check className="w-4 h-4" />
              {t("copied")}
            </>
          ) : (
            <>
              <Copy className="w-4 h-4" />
              {isCopying ? t("copying") : t("useThis")}
            </>
          )}
        </button>
      </div>
    </Modal>
  );
}

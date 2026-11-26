import React, { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

interface EditorTitleBarProps {
  fileId?: string;
  title: string;
  disabled: boolean;
  isReviewMode: boolean;
  autoFocusTitle: boolean;
  onAutoFocusConsumed?: () => void;
  onTitleChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onFocusBody: () => void;
}

export const EditorTitleBar: React.FC<EditorTitleBarProps> = ({
  fileId,
  title,
  disabled,
  isReviewMode,
  autoFocusTitle,
  onAutoFocusConsumed,
  onTitleChange,
  onFocusBody,
}) => {
  const { t } = useTranslation(["editor"]);
  const titleInputRef = useRef<HTMLInputElement>(null);

  // Focus & select the default title of a freshly created file on desktop so the author can rename & press Enter to write immediately
  useEffect(() => {
    if (!autoFocusTitle || disabled) return;
    const input = titleInputRef.current;
    if (!input) return;
    const frame = requestAnimationFrame(() => {
      input.focus({ preventScroll: true });
      input.select();
      onAutoFocusConsumed?.();
    });
    return () => cancelAnimationFrame(frame);
  }, [autoFocusTitle, fileId, disabled, onAutoFocusConsumed]);

  return (
    <div
      className={`shrink-0 px-4 sm:px-6 ${
        isReviewMode
          ? "bg-[hsl(var(--bg-canvas))] pb-1.5 pt-2"
          : "bg-transparent pt-5 pb-3"
      }`}
    >
      <input
        ref={titleInputRef}
        type="text"
        value={title}
        onChange={onTitleChange}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229) {
            e.preventDefault();
            onFocusBody();
          }
        }}
        disabled={disabled}
        placeholder={t("editor:placeholder.titlePlaceholder")}
        className={`w-full bg-transparent font-[family-name:var(--font-display)] font-bold text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] outline-none border-none ${
          isReviewMode ? "text-[1.7rem] leading-tight" : "text-xl"
        }`}
      />
    </div>
  );
};

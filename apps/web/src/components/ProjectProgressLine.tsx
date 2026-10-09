import React from "react";
import { useTranslation } from "react-i18next";
import { PenLine } from "lucide-react";
import type { ProjectProgress } from "../types";

interface ProjectProgressLineProps {
  progress: ProjectProgress | undefined;
  projectType: string | undefined;
}

/** "写到第几章 / 多少字", or a nudge when the outline is ready but nothing is written yet. */
export const ProjectProgressLine: React.FC<ProjectProgressLineProps> = ({ progress, projectType }) => {
  const { t, i18n } = useTranslation(["dashboard"]);
  if (!progress) return null;

  let label: string | null = null;
  if (progress.written_units > 0) {
    // "3.2万" / "32.5K": compact numbers in the UI language.
    const compact = new Intl.NumberFormat(i18n.language || "zh", {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(progress.word_count);
    const wordsText = t("dashboard:projectProgress.words", { count: progress.word_count, words: compact });
    label =
      projectType === "screenplay"
        ? t("dashboard:projectProgress.episodes", { count: progress.written_units, words: wordsText })
        : projectType === "short"
          ? t("dashboard:projectProgress.shortWritten", { words: wordsText })
          : t("dashboard:projectProgress.chapters", { count: progress.written_units, words: wordsText });
  } else if (progress.framework_ready) {
    label = t("dashboard:projectProgress.frameworkReady");
  }
  if (!label) return null;

  return (
    <div
      data-testid="project-progress"
      className="mb-2 flex items-center gap-1 text-xs text-[hsl(var(--text-secondary))]"
    >
      <PenLine className="w-3 h-3 shrink-0" />
      <span className="truncate">{label}</span>
    </div>
  );
};

import { useId } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../lib/i18n";
import type { MaterialNovel } from "../../lib/materialsApi";
import { Loader2 } from "../icons";
import { MaterialReadingContent as MarkdownContent } from "./MaterialReadingContent";
import { formatFileSize, isActiveMaterialStatus, type MaterialTreeItem } from "./materialHelpers";

export interface MaterialOverviewProps {
  material: MaterialNovel;
  folders: MaterialTreeItem[];
  onCategoryClick: (folderId: string) => void;
}

export function MaterialOverview({ material, folders, onCategoryClick }: MaterialOverviewProps) {
  const { t } = useTranslation(["materials"]);
  const titleId = useId();
  const source = material.source_meta;
  const sourceChapterCount = source?.source_chapter_count ?? material.source_chapter_count;
  const trialLimit = source?.trial_chapter_limit ?? material.trial_chapter_limit;
  const includedChapterCount = source?.chapter_count;
  const isKnownCompleteSource =
    includedChapterCount != null &&
    sourceChapterCount != null &&
    includedChapterCount === sourceChapterCount;

  const coverageMessage =
    includedChapterCount != null && sourceChapterCount != null && includedChapterCount < sourceChapterCount
      ? t("materials:detail.trialCoverageExact", {
          included: includedChapterCount,
          source: sourceChapterCount,
          count: includedChapterCount,
        })
      : trialLimit != null && !isKnownCompleteSource
        ? t("materials:detail.trialCoverageMaximum", { limit: trialLimit, count: trialLimit })
        : null;

  const sourceFacts = [
    {
      label: t("materials:detail.originalFilename"),
      value: source?.original_filename ?? material.original_filename,
    },
    {
      label: t("materials:detail.fileSize"),
      value:
        source?.file_size != null
          ? formatFileSize(source.file_size, i18n.language)
          : material.file_size != null
            ? formatFileSize(material.file_size, i18n.language)
            : undefined,
    },
    {
      label: t("materials:detail.includedCharacterCount"),
      value: source?.char_count == null ? undefined : source.char_count.toLocaleString(i18n.language),
    },
    {
      label: t("materials:detail.includedChapterCount"),
      value: includedChapterCount == null ? undefined : includedChapterCount.toLocaleString(i18n.language),
    },
    {
      label: t("materials:detail.sourceCharacterCount"),
      value: source?.source_char_count == null ? undefined : source.source_char_count.toLocaleString(i18n.language),
    },
    {
      label: t("materials:detail.sourceChapterCount"),
      value: sourceChapterCount == null ? undefined : sourceChapterCount.toLocaleString(i18n.language),
    },
    {
      label: t("materials:detail.trialChapterLimit"),
      value: trialLimit == null ? undefined : trialLimit.toLocaleString(i18n.language),
    },
  ].filter((fact): fact is { label: string; value: string } => typeof fact.value === "string" && fact.value.length > 0);

  return (
    <article className="material-detail material-overview" aria-labelledby={titleId}>
      <header className="material-overview-header">
        <h2 id={titleId} className="text-2xl font-semibold leading-snug tracking-tight text-[hsl(var(--text-primary))]">
          {t("materials:detail.overview")}
        </h2>
        {material.author && (
          <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
            {t("materials:detail.author")} · <span className="text-[hsl(var(--text-primary))]">{material.author}</span>
          </p>
        )}
        {isActiveMaterialStatus(material.status) && (
          <div className="mt-4 flex items-center gap-2 text-sm text-[hsl(var(--text-secondary))]">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>{t(`materials:status.${material.status}`)}</span>
          </div>
        )}
      </header>

      {coverageMessage && (
        <p className="material-overview-coverage" role="note">{coverageMessage}</p>
      )}

      {material.synopsis && (
        <section className="material-overview-section" aria-labelledby={`${titleId}-synopsis`}>
          <h3 id={`${titleId}-synopsis`}>{t("materials:detail.synopsis")}</h3>
          <MarkdownContent content={material.synopsis} />
        </section>
      )}

      <section className="material-overview-section" aria-labelledby={`${titleId}-index`}>
        <h3 id={`${titleId}-index`}>{t("materials:detail.categoryIndex")}</h3>
        <div className="material-overview-index">
          {folders.map((folder) => (
            <button
              key={folder.id}
              type="button"
              aria-label={
                folder.count === undefined
                  ? t("materials:detail.openCategoryUnknown", { category: folder.title })
                  : t("materials:detail.openCategoryCount", {
                      category: folder.title,
                      count: folder.count,
                    })
              }
              onClick={() => onCategoryClick(folder.id)}
            >
              <span>{folder.title}</span>
              <span className="tabular-nums">{folder.count === undefined ? "—" : folder.count.toLocaleString(i18n.language)}</span>
            </button>
          ))}
        </div>
      </section>

      {sourceFacts.length > 0 && (
        <section className="material-overview-section" aria-labelledby={`${titleId}-source`}>
          <h3 id={`${titleId}-source`}>{t("materials:detail.sourceFacts")}</h3>
          <dl className="material-overview-facts tabular-nums">
            {sourceFacts.map((fact) => (
              <div key={fact.label}>
                <dt>{fact.label}</dt>
                <dd>{fact.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </article>
  );
}

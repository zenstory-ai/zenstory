import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { LazyMarkdown } from "../LazyMarkdown";
import { ChevronDown, Zap } from "../icons";
import { publicSkillsApi } from "../../lib/api";
import { logger } from "../../lib/logger";
import { toast } from "../../lib/toast";
import type { PublicSkill } from "../../types";
import { AnimatedCollapse } from "../ui/AnimatedCollapse";
import { Badge } from "../ui/Badge";

export interface PublicSkillCardProps {
  skill: PublicSkill;
  isAdded: boolean;
  adding: boolean;
  onAdd: () => void;
  isMobile?: boolean;
}

export function PublicSkillCard({
  skill,
  isAdded,
  adding,
  onAdd,
  isMobile,
}: PublicSkillCardProps) {
  const { t } = useTranslation(["skills"]);
  const [expanded, setExpanded] = useState(false);
  const [fullInstructions, setFullInstructions] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const canExpand =
    skill.source !== "official" &&
    !skill.instructions_hidden &&
    (Boolean(skill.instructions.trim()) || Boolean(skill.instructions_truncated));

  const handleToggleExpanded = async () => {
    const next = !expanded;
    setExpanded(next);
    // 列表只带正文预览；展开时再取完整正文
    if (!next || !canExpand || !skill.instructions_truncated || fullInstructions !== null) return;
    setLoadingDetail(true);
    try {
      const detail = await publicSkillsApi.get(skill.id);
      if (!mountedRef.current) return;
      setFullInstructions(detail.instructions);
    } catch (error) {
      if (!mountedRef.current) return;
      logger.error("Failed to load public skill detail:", error);
      toast.error(error instanceof Error && error.message ? error.message : t("skills:errors.detailFailed"));
    } finally {
      if (mountedRef.current) {
        setLoadingDetail(false);
      }
    }
  };

  const instructions = fullInstructions ?? skill.instructions;

  return (
    <div data-testid="public-skill-card" className={`flex flex-col h-full bg-[hsl(var(--bg-secondary))] rounded-2xl border border-[hsl(var(--border-color))] transition-colors ${isMobile ? "p-3" : "p-5"}`}>
      {/* Header */}
      <div className="flex items-center gap-3 mb-3">
        <div className="w-8 h-8 rounded-lg bg-[hsl(var(--accent-primary)/0.08)] flex items-center justify-center shrink-0">
          <Zap className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-[hsl(var(--text-primary))] text-sm truncate">{skill.name}</h3>
        </div>
        {skill.source === "official" && (
          <Badge variant="info" className="shrink-0">
            {t("official")}
          </Badge>
        )}
      </div>

      {/* Description - fixed 2-line height for alignment */}
      <p className="text-xs leading-relaxed text-[hsl(var(--text-secondary))] line-clamp-2 min-h-[2.6em] mb-4">
        {skill.description || "\u00A0"}
      </p>

      {/* Footer - pushed to bottom */}
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 mt-auto pt-3 border-t border-[hsl(var(--border-color)/0.5)]">
        {canExpand && (
          <button
            onClick={() => void handleToggleExpanded()}
            aria-expanded={expanded}
            className="min-h-11 shrink-0 whitespace-nowrap text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] flex items-center gap-1 transition-colors"
          >
            <ChevronDown className={`w-3 h-3 motion-chevron ${expanded ? "rotate-180" : ""}`} />
            <span>{expanded ? t("collapse") : t("expand")}</span>
          </button>
        )}

        <div className={`flex max-w-full flex-wrap items-center gap-3 ${canExpand ? "" : "w-full justify-between"}`}>
          <span className="shrink-0 text-xs text-[hsl(var(--text-tertiary))]">
            {skill.add_count} {t("addCount")}
          </span>
          <button
            onClick={onAdd}
            disabled={isAdded || adding}
            className={`min-h-11 whitespace-nowrap px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              isAdded
                ? "text-[hsl(var(--text-tertiary))] cursor-default"
                : "text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.08)]"
            }`}
          >
            {adding ? (
              <div className="w-3 h-3 animate-spin rounded-full border-2 border-[hsl(var(--accent-primary))] border-t-transparent" />
            ) : isAdded ? (
              <span>{t("alreadyAdded")}</span>
            ) : (
              <span>{t("addToMine")}</span>
            )}
          </button>
        </div>
      </div>

      {/* Expanded instructions */}
      <AnimatedCollapse open={Boolean(canExpand && expanded && instructions)}>
        <div className="mt-3 pt-3 border-t border-[hsl(var(--border-color)/0.5)]">
          <div className="markdown-content bg-[hsl(var(--bg-tertiary))] rounded-xl p-3 text-xs max-h-48 overflow-y-auto">
            {loadingDetail ? (
              <div className="flex justify-center py-4">
                <div className="w-4 h-4 animate-spin rounded-full border-2 border-[hsl(var(--accent-primary))] border-t-transparent" />
              </div>
            ) : (
              <LazyMarkdown>{instructions || ""}</LazyMarkdown>
            )}
          </div>
        </div>
      </AnimatedCollapse>
    </div>
  );
}

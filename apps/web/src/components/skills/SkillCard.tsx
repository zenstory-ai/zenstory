import { useState } from "react";
import { useTranslation } from "react-i18next";
import { LazyMarkdown } from "../LazyMarkdown";
import { ChevronDown, MinusCircle, Pencil, Share2, Trash2, Zap } from "../icons";
import type { AddedSkill, Skill } from "../../types";
import { AnimatedCollapse } from "../ui/AnimatedCollapse";
import { IconButton } from "../ui/IconButton";
import { SkillResourcesSection } from "./SkillResourcesSection";
import { getLocalizedSkillCategory } from "./skillHelpers";
import { ExportSkillButton, ShareStatusBadge, SkillSelectionCheckbox } from "./SkillSharedPrimitives";

export interface SkillCardProps {
  skill: Skill;
  onEdit?: () => void;
  onDelete?: () => void;
  onShare?: () => void;
  onExport?: () => void;
  exporting?: boolean;
  isMobile?: boolean;
  isSelected?: boolean;
  onToggleSelect?: () => void;
}

export function SkillCard({
  skill,
  onEdit,
  onDelete,
  onShare,
  onExport,
  exporting,
  isMobile,
  isSelected,
  onToggleSelect,
}: SkillCardProps) {
  const { t } = useTranslation(["skills"]);
  const [expanded, setExpanded] = useState(false);

  const actions = (
    <div
      data-testid="skill-card-actions"
      className={isMobile ? "mt-2 flex items-center justify-end gap-1" : "flex items-center gap-1 shrink-0"}
    >
      <IconButton
        onClick={onEdit}
        label={t("skills:editSkill")}
        icon={<Pencil className="w-4 h-4" />}
      />
      <IconButton
        onClick={onShare}
        label={t("skills:share.title")}
        icon={<Share2 className="w-4 h-4" />}
      />
      {onExport && (
        <ExportSkillButton onExport={onExport} exporting={exporting} isMobile={isMobile} />
      )}
      <IconButton
        onClick={() => setExpanded(!expanded)}
        label={expanded ? t("skills:collapse") : t("skills:expand")}
        aria-expanded={expanded}
        icon={<ChevronDown className={`w-4 h-4 motion-chevron ${expanded ? "rotate-180" : ""}`} />}
      />
      <span aria-hidden="true" className="mx-0.5 h-4 w-px bg-[hsl(var(--separator-color))]" />
      <IconButton
        onClick={onDelete}
        label={t("skills:deleteConfirm.title")}
        tone="danger"
        icon={<Trash2 className="w-4 h-4" />}
      />
    </div>
  );

  return (
    <div
      data-testid="skill-card"
      className={`group relative rounded-2xl border ${
        isSelected
          ? "border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.03)]"
          : "border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.3)]"
      } transition-colors ${isMobile ? "p-3" : "p-4"}`}
    >
      <div className="flex items-start justify-between gap-3">
        {onToggleSelect && (
          <SkillSelectionCheckbox
            skillName={skill.name}
            isSelected={isSelected}
            onToggleSelect={onToggleSelect}
          />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <div className="w-9 h-9 rounded-lg bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center group-hover:scale-110 transition-transform shrink-0">
              <Zap className={`text-[hsl(var(--accent-primary))] ${isMobile ? "w-4 h-4" : "w-4.5 h-4.5"}`} />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-[hsl(var(--text-primary))] truncate leading-snug">
                {skill.name}
              </h3>
            </div>
            {skill.share_status && <ShareStatusBadge status={skill.share_status} />}
          </div>
          {skill.description && (
            <p className="text-xs text-[hsl(var(--text-secondary))] mb-2.5 line-clamp-2">
              {skill.description}
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {skill.triggers.slice(0, isMobile ? 3 : 5).map((trigger, i) => (
              <span
                key={i}
                className="text-xs px-2 py-0.5 rounded-full bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]"
              >
                {trigger}
              </span>
            ))}
            {skill.triggers.length > (isMobile ? 3 : 5) && (
              <span className="text-xs text-[hsl(var(--text-tertiary))]">
                +{skill.triggers.length - (isMobile ? 3 : 5)}
              </span>
            )}
            {!!skill.resource_count && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
                {t("skills:resources.count", { count: skill.resource_count })}
              </span>
            )}
          </div>
        </div>

        {!isMobile && actions}
      </div>
      {/* Phones: five 44px actions would squeeze the title to nothing, so they get their own row. */}
      {isMobile && actions}

      {/* Instructions - Markdown rendered */}
      <AnimatedCollapse open={expanded && Boolean(skill.instructions)}>
        <div className="mt-3 pt-3 border-t border-[hsl(var(--border-color))]">
          <div className="flex items-center gap-1.5 mb-2">
            <span className="text-xs font-medium text-[hsl(var(--text-tertiary))]">
              {t("form.instructions")}
            </span>
          </div>
          <div className={`markdown-content bg-[hsl(var(--bg-tertiary))] rounded-xl ${isMobile ? "p-3 text-xs" : "p-4 text-sm"}`}>
            <LazyMarkdown>{skill.instructions}</LazyMarkdown>
          </div>
        </div>
      </AnimatedCollapse>
    </div>
  );
}

export interface AddedSkillCardProps {
  skill: AddedSkill;
  onRemove: () => void;
  removing?: boolean;
  onExport?: () => void;
  exporting?: boolean;
  isMobile?: boolean;
  isSelected?: boolean;
  onToggleSelect?: () => void;
}

/** Card for skills added from the public library. */
export function AddedSkillCard({
  skill,
  onRemove,
  removing,
  onExport,
  exporting,
  isMobile,
  isSelected,
  onToggleSelect,
}: AddedSkillCardProps) {
  const { t } = useTranslation(["skills"]);
  const [expanded, setExpanded] = useState(false);
  // 官方技能的正文不对用户返回：不能导出，展开只显示完整描述和说明
  const hidden = !!skill.instructions_hidden;

  const hasLeadingAction = Boolean((onExport && !hidden) || (!hidden && skill.instructions.trim()));
  const actions = (
    <div
      data-testid="skill-card-actions"
      className={isMobile ? "mt-2 flex items-center justify-end gap-1" : "flex items-center gap-1 shrink-0"}
    >
      {onExport && !hidden && (
        <ExportSkillButton onExport={onExport} exporting={exporting} isMobile={isMobile} />
      )}
      {!hidden && Boolean(skill.instructions.trim()) && (
        <IconButton
          onClick={() => setExpanded(!expanded)}
          label={expanded ? t("skills:collapse") : t("skills:expand")}
          aria-expanded={expanded}
          icon={<ChevronDown className={`w-4 h-4 motion-chevron ${expanded ? "rotate-180" : ""}`} />}
        />
      )}
      {hasLeadingAction && (
        <span aria-hidden="true" className="mx-0.5 h-4 w-px bg-[hsl(var(--separator-color))]" />
      )}
      <IconButton
        onClick={onRemove}
        disabled={removing}
        label={t("skills:remove")}
        tone="danger"
        icon={
          removing ? (
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current" />
          ) : (
            <MinusCircle className="w-4 h-4" />
          )
        }
      />
    </div>
  );
  const localizedCategory = getLocalizedSkillCategory(skill.category, t);

  return (
    <div
      data-testid="skill-card"
      className={`group relative rounded-2xl border ${
        isSelected
          ? "border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.03)]"
          : "border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.3)]"
      } transition-colors ${isMobile ? "p-3" : "p-4"}`}
    >
      <div className="flex items-start justify-between gap-3">
        {onToggleSelect && (
          <SkillSelectionCheckbox
            skillName={skill.name}
            isSelected={isSelected}
            onToggleSelect={onToggleSelect}
          />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <div className="w-9 h-9 rounded-lg bg-[hsl(var(--bg-tertiary))] flex items-center justify-center group-hover:scale-110 transition-transform shrink-0">
              <Zap className={`text-[hsl(var(--text-secondary))] ${isMobile ? "w-4 h-4" : "w-4.5 h-4.5"}`} />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-[hsl(var(--text-primary))] truncate leading-snug">
                {skill.name}
              </h3>
            </div>
            <span className="text-xs px-1.5 py-0.5 rounded bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-tertiary))] shrink-0">
              {t("skills:added")}
            </span>
          </div>
          {skill.description && (
            <p className={`text-xs text-[hsl(var(--text-secondary))] mb-2.5 ${hidden ? "" : "line-clamp-2"}`}>
              {skill.description}
            </p>
          )}
          <div className="flex items-center gap-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
              {localizedCategory}
            </span>
          </div>
        </div>

        {!isMobile && actions}
      </div>
      {isMobile && actions}

      {/* Instructions - Markdown rendered */}
      <AnimatedCollapse open={expanded && !hidden && Boolean(skill.instructions)}>
        <div className="mt-3 pt-3 border-t border-[hsl(var(--border-color))]">
          <div className="flex items-center gap-1.5 mb-2">
            <span className="text-xs font-medium text-[hsl(var(--text-tertiary))]">
              {t("form.instructions")}
            </span>
          </div>
          <div className={`markdown-content bg-[hsl(var(--bg-tertiary))] rounded-xl ${isMobile ? "p-3 text-xs" : "p-4 text-sm"}`}>
            <LazyMarkdown>{skill.instructions}</LazyMarkdown>
          </div>
          <div className="mt-3">
            <SkillResourcesSection skillId={skill.id} readOnly isMobile={isMobile} />
          </div>
        </div>
      </AnimatedCollapse>
    </div>
  );
}

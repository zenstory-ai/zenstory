import { useTranslation } from "react-i18next";
import { Button } from "../ui/Button";

export interface MaterialTeaserViewProps {
  proDecompositionsLimit: number | null;
  trialAvailable: boolean;
  trialMaxChapters?: number;
  trialCharacterLimit: string;
  proEntryLabel: string;
  actionSize: "md" | "touch";
  onStartTrial: () => void;
  onOpenUpgrade: (destination: "billing" | "pricing") => void;
}

export function MaterialTeaserView({
  proDecompositionsLimit,
  trialAvailable,
  trialMaxChapters,
  trialCharacterLimit,
  proEntryLabel,
  actionSize,
  onStartTrial,
  onOpenUpgrade,
}: MaterialTeaserViewProps) {
  const { t } = useTranslation(["materials"]);

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-6">
        <div className="max-w-3xl space-y-4">
          <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t("materials:teaserTitle", {
              defaultValue: "上传参考小说，一键拆出章节梗概、角色和世界观",
            })}
          </h2>
          <p className="text-sm leading-6 text-[hsl(var(--text-secondary))]">
            {proDecompositionsLimit === null
              ? t("materials:teaserDescriptionNoLimit", {
                  defaultValue: "开通 Pro 后，每月都能拆解参考小说。",
                })
              : t("materials:teaserDescription", {
                  defaultValue: "开通 Pro 后，每月可拆解 {{limit}} 次。",
                  limit: proDecompositionsLimit,
                })}
          </p>
          <ul className="space-y-2 text-sm text-[hsl(var(--text-secondary))]">
            <li>• {t("materials:teaserFeatureOne", { defaultValue: "拆出章节梗概、角色、世界观和金手指" })}</li>
            <li>• {t("materials:teaserFeatureTwo", { defaultValue: "把角色和设定添加到项目，或引用到 AI 对话" })}</li>
            <li>• {t("materials:teaserFeatureThree", { defaultValue: "次数用完后，已拆好的内容仍可查看和引用" })}</li>
          </ul>
          {!trialAvailable && (
            <p data-testid="materials-free-try" className="text-sm text-[hsl(var(--text-secondary))]">
              {t("materials:teaserFreeTry", {
                defaultValue: "素材库免费试拆暂未开放。开通 Pro 后，可上传参考小说自动拆解，并在写作时引用拆解结果。",
              })}
            </p>
          )}
          <p data-testid="materials-example-source" className="pt-2 text-xs text-[hsl(var(--text-tertiary))]">
            {t("materials:teaserExampleSource", {
              defaultValue: "拆解示例 ·《九天斩神诀》· 小知了 · 前 3 章",
            })}
          </p>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] p-4">
              <div className="text-xs font-medium text-[hsl(var(--accent-primary))]">
                {t("materials:teaserCardCharacter", {
                  defaultValue: "角色 · 秦月儿",
                })}
              </div>
              <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
                {t("materials:teaserCardCharacterBody", {
                  defaultValue: "林辰的未婚妻，却选择入宗机会与力量：探狱时撕下伪装，向落难的林辰索取唯一的入宗信物青玄令。",
                })}
              </p>
            </div>
            <div className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] p-4">
              <div className="text-xs font-medium text-[hsl(var(--accent-primary))]">
                {t("materials:teaserCardSynopsis", {
                  defaultValue: "章节梗概 · 前 3 章",
                })}
              </div>
              <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
                {t("materials:teaserCardSynopsisBody", {
                  defaultValue: "少帅林辰蒙冤待斩 → 玉佩带来重修机会 → 未婚妻摊牌索要信物。第 3 章结尾他刚恢复聚力境一重，脱困仍悬而未决。",
                })}
              </p>
            </div>
            <div className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] p-4">
              <div className="text-xs font-medium text-[hsl(var(--accent-primary))]">
                {t("materials:teaserCardWorldview", {
                  defaultValue: "设定 · 天钨",
                })}
              </div>
              <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
                {t("materials:teaserCardWorldviewBody", {
                  defaultValue: "能穿透禁制与大阵、遮掩气息、遁入黑暗。丹田虽已修复，重伤未愈：力量有用，危机仍在。",
                })}
              </p>
            </div>
          </div>
          {trialAvailable && (
            <p data-testid="materials-trial-limits" className="pt-2 text-sm text-[hsl(var(--text-secondary))]">
              {t("materials:trialLimits", {
                defaultValue: "每个账号仅 1 次：前 {{chapters}} 章，合计不超过 {{characters}} 字。提交成功后即消耗机会。",
                chapters: trialMaxChapters,
                characters: trialCharacterLimit,
              })}
            </p>
          )}
          <div className="flex flex-wrap gap-3 pt-2">
            {trialAvailable && (
              <Button
                size={actionSize}
                data-testid="materials-trial-start"
                onClick={onStartTrial}
              >
                {t("materials:trialStart", {
                  defaultValue: "免费试拆前 {{chapters}} 章",
                  chapters: trialMaxChapters,
                })}
              </Button>
            )}
            {/* With the trial on offer, the header keeps the Pro entry. */}
            {!trialAvailable && (
              <Button size={actionSize} onClick={() => onOpenUpgrade("billing")}>
                {proEntryLabel}
              </Button>
            )}
            <Button variant="secondary" size={actionSize} onClick={() => onOpenUpgrade("pricing")}>
              {t("materials:teaserSecondary", { defaultValue: "查看套餐对比" })}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

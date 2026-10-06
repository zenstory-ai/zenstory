import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BarChart3,
  BookOpen,
  Briefcase,
  CalendarCheck,
  Check,
  Compass,
  Lightbulb,
  Sparkles,
  Target,
  Users,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "../lib/utils";
import { toast } from "../lib/toast";
import {
  getPersonaOnboardingData,
  savePersonaOnboardingData,
  type PersonaExperienceLevel,
} from "../lib/onboardingPersona";
import { onboardingPersonaApi, personaOnboardingQueryKey } from "../lib/onboardingPersonaApi";

const MAX_PERSONA_SELECTION = 3;

type PersonaId = "explorer" | "serial" | "professional" | "fanfic" | "studio";
type GoalId = "finishBook" | "buildHabit" | "improveQuality" | "growAudience" | "monetize";

interface PersonaOption {
  id: PersonaId;
  icon: LucideIcon;
  badgeVariant: "info" | "purple" | "cyan" | "success" | "warning";
}

interface GoalOption {
  id: GoalId;
  icon: LucideIcon;
}

const PERSONA_OPTIONS: PersonaOption[] = [
  { id: "explorer", icon: Compass, badgeVariant: "info" },
  { id: "serial", icon: CalendarCheck, badgeVariant: "purple" },
  { id: "professional", icon: Briefcase, badgeVariant: "warning" },
  { id: "fanfic", icon: Lightbulb, badgeVariant: "cyan" },
  { id: "studio", icon: Users, badgeVariant: "success" },
];

const GOAL_OPTIONS: GoalOption[] = [
  { id: "finishBook", icon: BookOpen },
  { id: "buildHabit", icon: CalendarCheck },
  { id: "improveQuality", icon: Sparkles },
  { id: "growAudience", icon: BarChart3 },
  { id: "monetize", icon: Target },
];

const EXPERIENCE_LEVELS: PersonaExperienceLevel[] = ["beginner", "intermediate", "advanced"];

interface OnboardingLocationState {
  from?: {
    pathname?: string;
    search?: string;
    hash?: string;
  };
}

const sanitizeNextPath = (
  from: OnboardingLocationState["from"] | undefined,
): string => {
  const pathname = from?.pathname;
  if (!pathname || !pathname.startsWith("/") || pathname.startsWith("/onboarding")) {
    return "/dashboard";
  }

  return `${pathname}${from?.search ?? ""}${from?.hash ?? ""}`;
};

export default function OnboardingPersonaPage() {
  const { t } = useTranslation(["onboarding", "common"]);
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const locationState = location.state as OnboardingLocationState | null;
  const nextPath = sanitizeNextPath(locationState?.from);

  const existingData = useMemo(
    () => (user ? getPersonaOnboardingData(user.id) : null),
    [user]
  );

  const [selectedPersonas, setSelectedPersonas] = useState<PersonaId[]>(
    (existingData?.selected_personas ?? []).filter((value): value is PersonaId =>
      PERSONA_OPTIONS.some((option) => option.id === value)
    )
  );
  const [selectedGoals, setSelectedGoals] = useState<GoalId[]>(
    (existingData?.selected_goals ?? []).filter((value): value is GoalId =>
      GOAL_OPTIONS.some((option) => option.id === value)
    )
  );
  const [experienceLevel, setExperienceLevel] = useState<PersonaExperienceLevel>(
    existingData?.experience_level ?? "beginner"
  );
  const [hasRestoredProfile, setHasRestoredProfile] = useState(Boolean(existingData));
  const [limitReached, setLimitReached] = useState(false);
  const [saving, setSaving] = useState(false);
  const personaQueryKey = personaOnboardingQueryKey(user?.id ?? "anonymous");
  const { data: serverState } = useQuery({
    queryKey: personaQueryKey,
    queryFn: onboardingPersonaApi.getState,
    enabled: Boolean(user),
  });

  useEffect(() => {
    setHasRestoredProfile(Boolean(existingData));
  }, [existingData]);

  useEffect(() => {
    if (!user || !serverState?.profile) return;

    const profile = serverState.profile;
    const normalizedPersonas = (profile.selected_personas ?? []).filter(
      (value): value is PersonaId => PERSONA_OPTIONS.some((option) => option.id === value)
    );
    const normalizedGoals = (profile.selected_goals ?? []).filter(
      (value): value is GoalId => GOAL_OPTIONS.some((option) => option.id === value)
    );
    const normalizedLevel: PersonaExperienceLevel =
      EXPERIENCE_LEVELS.includes(profile.experience_level)
        ? profile.experience_level
        : "beginner";

    setSelectedPersonas(normalizedPersonas);
    setSelectedGoals(normalizedGoals);
    setExperienceLevel(normalizedLevel);
    setHasRestoredProfile(true);

    savePersonaOnboardingData(user.id, {
      selected_personas: normalizedPersonas,
      selected_goals: normalizedGoals,
      experience_level: normalizedLevel,
      skipped: Boolean(profile.skipped),
    });
  }, [serverState, user]);

  const selectedPersonaLabels = useMemo(
    () =>
      selectedPersonas.map((id) =>
        t(`onboarding:persona.options.${id}.title`, id)
      ),
    [selectedPersonas, t]
  );

  const personalizedTips = useMemo(() => {
    const tips: string[] = [];

    if (selectedPersonas.includes("explorer")) {
      tips.push(t("onboarding:preview.items.explorer", "灵感模板和快速起稿入口"));
    }
    if (selectedPersonas.includes("serial")) {
      tips.push(t("onboarding:preview.items.serial", "连续写作天数统计"));
    }
    if (selectedPersonas.includes("professional")) {
      tips.push(t("onboarding:preview.items.professional", "大纲、章节到改稿的高效流程"));
    }
    if (selectedPersonas.includes("fanfic")) {
      tips.push(t("onboarding:preview.items.fanfic", "整理角色与设定，AI 写作时参考"));
    }
    if (selectedPersonas.includes("studio")) {
      tips.push(t("onboarding:preview.items.studio", "每部作品一个项目，分开管理"));
    }

    if (selectedGoals.includes("monetize")) {
      tips.push(t("onboarding:preview.items.monetize", "免费版与 Pro 的套餐对比"));
    }
    if (selectedGoals.includes("improveQuality")) {
      tips.push(t("onboarding:preview.items.quality", "让 AI 审读并修改章节"));
    }

    tips.push(t(`onboarding:preview.level.${experienceLevel}`, "按你的写作经验给出建议"));

    return Array.from(new Set(tips)).slice(0, 4);
  }, [experienceLevel, selectedGoals, selectedPersonas, t]);

  const canSubmit = selectedPersonas.length > 0;

  const togglePersona = (id: PersonaId) => {
    setSelectedPersonas((prev) => {
      setLimitReached(false);
      if (prev.includes(id)) {
        return prev.filter((item) => item !== id);
      }

      if (prev.length >= MAX_PERSONA_SELECTION) {
        setLimitReached(true);
        return prev;
      }

      return [...prev, id];
    });
  };

  const toggleGoal = (id: GoalId) => {
    setSelectedGoals((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const handleSubmit = async (skip = false) => {
    if (!user || saving) return;
    if (!skip && !canSubmit) return;

    setSaving(true);
    const payload = {
      selected_personas: skip ? [] : selectedPersonas,
      selected_goals: skip ? [] : selectedGoals,
      experience_level: experienceLevel,
      skipped: skip,
    };

    try {
      await queryClient.cancelQueries({ queryKey: personaQueryKey });
      const result = await onboardingPersonaApi.save(payload);
      const profile = result.profile;
      if (!profile) {
        throw new Error("Persona onboarding save returned no profile");
      }

      savePersonaOnboardingData(user.id, {
        selected_personas: profile.selected_personas,
        selected_goals: profile.selected_goals,
        experience_level: profile.experience_level,
        skipped: profile.skipped,
      });
      queryClient.setQueryData(personaQueryKey, result);
      navigate(nextPath, {
        replace: true,
        state: nextPath === "/dashboard" ? { startDashboardCoachmark: true } : undefined,
      });
    } catch {
      toast.error(t("onboarding:errors.saveFailed", "保存失败，请检查网络后重试"));
    } finally {
      setSaving(false);
    }
  };

  if (!user) return null;

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))] relative overflow-hidden">
      <div className="pointer-events-none absolute -top-24 -left-20 h-72 w-72 rounded-full bg-[hsl(var(--accent-primary)/0.12)] blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 right-0 h-80 w-80 rounded-full bg-[hsl(var(--purple)/0.14)] blur-3xl" />

      <div className="relative max-w-6xl mx-auto px-4 sm:px-6 py-8 sm:py-10">
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <Badge variant="purple">{t("onboarding:hero.step", "新用户引导")}</Badge>
          <Badge variant="info">{t("onboarding:hero.badge", "2 分钟完成")}</Badge>
        </div>

        <h1 className="text-2xl sm:text-3xl font-bold text-[hsl(var(--text-primary))]">
          {t("onboarding:hero.title", "告诉我们你怎么写作")}
        </h1>
        <p className="mt-2 text-sm sm:text-base text-[hsl(var(--text-secondary))] max-w-3xl">
          {t("onboarding:hero.subtitle", "回答 3 个问题，也可以直接跳过。")}
        </p>

        {hasRestoredProfile && (
          <div className="mt-4 inline-flex items-center gap-2 rounded-lg border border-[hsl(var(--accent-primary)/0.35)] bg-[hsl(var(--accent-primary)/0.1)] px-3 py-1.5 text-xs text-[hsl(var(--accent-primary))]">
            <Check className="w-3.5 h-3.5" />
            {t("onboarding:hero.restore", "已带入你上次的选择")}
          </div>
        )}

        <div className="mt-8 grid grid-cols-1 lg:grid-cols-[1.35fr_0.9fr] gap-5">
          <div className="space-y-5">
            <Card variant="outlined" padding="lg" className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
                    {t("onboarding:persona.title", "你是哪类创作者？")}
                  </h2>
                  <p className="text-xs sm:text-sm text-[hsl(var(--text-secondary))] mt-1">
                    {t("onboarding:persona.subtitle", "最多选 3 项。")}
                  </p>
                </div>
                <Badge variant="neutral">
                  {t("onboarding:persona.counter", "{{selected}} / {{max}} 已选", {
                    selected: selectedPersonas.length,
                    max: MAX_PERSONA_SELECTION,
                  })}
                </Badge>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {PERSONA_OPTIONS.map((option) => {
                  const Icon = option.icon;
                  const selected = selectedPersonas.includes(option.id);

                  return (
                    <button
                      key={option.id}
                      type="button"
                      onClick={() => togglePersona(option.id)}
                      aria-pressed={selected}
                      className={cn(
                        "text-left rounded-xl border p-4 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]",
                        selected
                          ? "border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.12)]"
                          : "border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.55)] hover:bg-[hsl(var(--bg-tertiary)/0.35)]"
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <Badge variant={option.badgeVariant} icon={<Icon className="w-3.5 h-3.5" />}>
                          {t(`onboarding:persona.options.${option.id}.tag`, "画像")}
                        </Badge>
                        {selected && (
                          <span className="rounded-full bg-[hsl(var(--accent-primary))] text-white p-1">
                            <Check className="w-3 h-3" />
                          </span>
                        )}
                      </div>
                      <p className="mt-3 text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {t(`onboarding:persona.options.${option.id}.title`, option.id)}
                      </p>
                      <p className="mt-1 text-xs text-[hsl(var(--text-secondary))] leading-relaxed">
                        {t(`onboarding:persona.options.${option.id}.desc`, "")}
                      </p>
                    </button>
                  );
                })}
              </div>

              {limitReached && (
                <p className="text-xs text-[hsl(var(--warning))]">
                  {t("onboarding:persona.limitReached", "最多选 3 项，请先取消一个。")}
                </p>
              )}
            </Card>

            <Card variant="outlined" padding="lg" className="space-y-4">
              <div>
                <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
                  {t("onboarding:goal.title", "你当前最想达成什么？")}
                </h2>
                <p className="text-xs sm:text-sm text-[hsl(var(--text-secondary))] mt-1">
                  {t("onboarding:goal.subtitle", "可多选。")}
                </p>
              </div>
              <div className="flex flex-wrap gap-2.5">
                {GOAL_OPTIONS.map((goal) => {
                  const Icon = goal.icon;
                  const active = selectedGoals.includes(goal.id);

                  return (
                    <button
                      key={goal.id}
                      type="button"
                      onClick={() => toggleGoal(goal.id)}
                      aria-pressed={active}
                      className={cn(
                        "inline-flex items-center gap-2 rounded-full border px-3.5 py-2 text-xs sm:text-sm transition-colors",
                        active
                          ? "border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.12)] text-[hsl(var(--accent-primary))]"
                          : "border-[hsl(var(--border-color))] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:border-[hsl(var(--accent-primary)/0.35)]"
                      )}
                    >
                      <Icon className="w-3.5 h-3.5" />
                      {t(`onboarding:goal.options.${goal.id}.title`, goal.id)}
                    </button>
                  );
                })}
              </div>
            </Card>

            <Card variant="outlined" padding="lg" className="space-y-4">
              <div>
                <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
                  {t("onboarding:experience.title", "你的写作经验")}
                </h2>
                <p className="text-xs sm:text-sm text-[hsl(var(--text-secondary))] mt-1">
                  {t("onboarding:experience.subtitle", "选最接近的一项。")}
                </p>
              </div>
              <div
                className="grid grid-cols-1 sm:grid-cols-3 gap-3"
                role="radiogroup"
                aria-label={t("onboarding:experience.title", "你的写作经验")}
              >
                {EXPERIENCE_LEVELS.map((level) => {
                  const active = experienceLevel === level;
                  return (
                    <button
                      key={level}
                      type="button"
                      onClick={() => setExperienceLevel(level)}
                      role="radio"
                      aria-checked={active}
                      className={cn(
                        "rounded-xl border px-3 py-3 text-left transition-colors",
                        active
                          ? "border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.12)]"
                          : "border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.35)]"
                      )}
                    >
                      <p
                        className={cn(
                          "text-sm font-semibold",
                          active
                            ? "text-[hsl(var(--accent-primary))]"
                            : "text-[hsl(var(--text-primary))]"
                        )}
                      >
                        {t(`onboarding:experience.options.${level}.title`, level)}
                      </p>
                      <p className="mt-1 text-xs text-[hsl(var(--text-secondary))] leading-relaxed">
                        {t(`onboarding:experience.options.${level}.desc`, "")}
                      </p>
                    </button>
                  );
                })}
              </div>
            </Card>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <Button
                variant="ghost"
                onClick={() => {
                  void handleSubmit(true);
                }}
                disabled={saving}
              >
                {t("onboarding:actions.skip", "跳过")}
              </Button>

              <Button
                onClick={() => {
                  void handleSubmit(false);
                }}
                disabled={!canSubmit || saving}
                isLoading={saving}
                loadingText={t("onboarding:actions.saving", "正在保存...")}
                rightIcon={<ArrowRight className="w-4 h-4" />}
              >
                {t("onboarding:actions.submit", "保存并进入工作台")}
              </Button>
            </div>
          </div>

          <Card variant="outlined" padding="lg" className="h-fit lg:sticky lg:top-6">
            <div className="flex items-center gap-2 text-[hsl(var(--text-primary))]">
              <Sparkles className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
              <h3 className="text-sm font-semibold">
                {t("onboarding:preview.title", "首页会为你推荐")}
              </h3>
            </div>

            <p className="mt-2 text-xs text-[hsl(var(--text-secondary))] leading-relaxed">
              {t("onboarding:preview.subtitle", "保存后，工作台首页会按这些选择推荐下一步。")}
            </p>

            {selectedPersonaLabels.length > 0 ? (
              <div className="mt-4 flex flex-wrap gap-2">
                {selectedPersonaLabels.map((label) => (
                  <Badge key={label} variant="purple">
                    {label}
                  </Badge>
                ))}
              </div>
            ) : (
              <div className="mt-4 rounded-lg border border-dashed border-[hsl(var(--border-color))] px-3 py-2 text-xs text-[hsl(var(--text-secondary))]">
                {t("onboarding:preview.empty", "选一个创作者类型，看看会推荐什么")}
              </div>
            )}

            <ul className="mt-4 space-y-2">
              {personalizedTips.map((tip, index) => (
                <li
                  key={`${tip}-${index}`}
                  className="rounded-lg bg-[hsl(var(--bg-tertiary)/0.55)] px-3 py-2 text-xs text-[hsl(var(--text-primary))] leading-relaxed"
                >
                  {tip}
                </li>
              ))}
            </ul>

            <p className="mt-4 text-[11px] text-[hsl(var(--text-tertiary))] leading-relaxed">
              {t("onboarding:preview.note", "这些选择保存在你的账号里，换设备也有效。")}
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}

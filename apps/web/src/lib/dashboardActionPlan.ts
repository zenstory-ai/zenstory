import { buildUpgradeUrl } from "../config/upgradeExperience";
import type { ProjectType } from "../types";
import type { PersonaRecommendation } from "./onboardingPersonaApi";
import type { ActivationGuideResponse } from "../types/writingStats";
import { inspirationsConfig } from "../config/inspirations";

type TranslateFn = (key: string, options?: { defaultValue?: string; [key: string]: unknown }) => string;

export type TodayActionPlanAction =
  | {
      type: "navigate";
      path: string;
    }
  | {
      type: "create_project";
      projectType: ProjectType;
    };

export interface TodayActionPlanItem {
  id: string;
  title: string;
  description: string;
  ctaLabel: string;
  action: TodayActionPlanAction;
}

interface BuildTodayActionPlanInput {
  activationGuide: ActivationGuideResponse | null;
  personaRecommendations: PersonaRecommendation[];
  projectsCount: number;
  latestProjectId: string | null;
  activeProjectType: ProjectType;
  t: TranslateFn;
}

function normalizeActionPath(path: string | null | undefined, fallback = "/dashboard"): string {
  const value = (path ?? "").trim();
  return value || fallback;
}

function isInspirationPath(path: string | null | undefined): boolean {
  const normalizedPath = normalizeActionPath(path);
  return normalizedPath === "/dashboard/inspirations"
    || normalizedPath.startsWith("/dashboard/inspirations/");
}

function isInspirationRecommendation(recommendation: PersonaRecommendation): boolean {
  return isInspirationPath(resolvePersonaRecommendationActionPath(recommendation));
}

function createActionId(item: TodayActionPlanItem): string {
  return `${item.id}:${item.action.type}:${item.action.type === "navigate" ? item.action.path : item.action.projectType}`;
}

function resolveActivationStepTitle(
  eventName: string,
  fallbackLabel: string,
  t: TranslateFn,
): string {
  return t(`activationGuide.steps.${eventName}`, { defaultValue: fallbackLabel });
}

function resolveActivationStepAction({
  eventName,
  stepActionPath,
  activationNextAction,
  latestProjectId,
  activeProjectType,
}: {
  eventName: string;
  stepActionPath: string | null | undefined;
  activationNextAction: string | null | undefined;
  latestProjectId: string | null;
  activeProjectType: ProjectType;
}): TodayActionPlanAction {
  if (eventName === "project_created") {
    return {
      type: "create_project",
      projectType: activeProjectType,
    };
  }

  if (eventName === "first_file_saved" || eventName === "first_ai_action_accepted") {
    if (latestProjectId) {
      return {
        type: "navigate",
        path: `/project/${latestProjectId}`,
      };
    }
  }

  return {
    type: "navigate",
    path: normalizeActionPath(stepActionPath, activationNextAction ?? "/dashboard"),
  };
}

function resolvePersonaRecommendationTitle(
  recommendation: PersonaRecommendation,
  t: TranslateFn,
): string {
  return t(`todayActionPlan.personaRecommendations.${recommendation.id}.title`, {
    defaultValue: recommendation.title,
  });
}

function resolvePersonaRecommendationDescription(
  recommendation: PersonaRecommendation,
  t: TranslateFn,
): string {
  const fallback = recommendation.description
    || t("todayActionPlan.personaDescription", {
      defaultValue: "根据你填写的写作偏好推荐。",
    });

  return t(`todayActionPlan.personaRecommendations.${recommendation.id}.description`, {
    defaultValue: fallback,
  });
}

function resolvePersonaRecommendationActionPath(
  recommendation: PersonaRecommendation,
): string {
  const raw = normalizeActionPath(recommendation.action, "/dashboard");
  if (raw !== "/dashboard") {
    return raw;
  }

  // Avoid no-op navigation (/dashboard -> /dashboard). Provide sensible fallbacks.
  const fallbackById: Record<string, string> = {
    persona_serial_streak: "/dashboard/projects",
    goal_quality_review: "/dashboard/projects",
    goal_habit_activation: "/dashboard/projects",
    level_beginner_path: "/dashboard/inspirations",
  };

  return fallbackById[recommendation.id] || "/dashboard/projects";
}

export function buildTodayActionPlan({
  activationGuide,
  personaRecommendations,
  projectsCount,
  latestProjectId,
  activeProjectType,
  t,
}: BuildTodayActionPlanInput): TodayActionPlanItem[] {
  const candidates: TodayActionPlanItem[] = [];
  const ctaLabel = t("todayActionPlan.cta", { defaultValue: "去完成" });

  const pendingActivationSteps = activationGuide?.within_first_day
    ? activationGuide.steps?.filter((step) => {
        if (step.completed) return false;
        if (inspirationsConfig.enabled) return true;
        const resolvedPath = normalizeActionPath(step.action_path, activationGuide.next_action ?? "/dashboard");
        return !isInspirationPath(resolvedPath);
      }) ?? []
    : [];
  const hasPendingActivationSteps = pendingActivationSteps.length > 0;
  const hasPendingActivationProjectStep = pendingActivationSteps.some(
    (step) => step.event_name === "project_created",
  );

  for (const step of pendingActivationSteps) {
    candidates.push({
      id: `activation-${step.event_name}`,
      title: resolveActivationStepTitle(step.event_name, step.label, t),
      description: t("todayActionPlan.activationDescription", {
        defaultValue: "上手必做的一步。",
      }),
      ctaLabel,
      action: resolveActivationStepAction({
        eventName: step.event_name,
        stepActionPath: step.action_path,
        activationNextAction: activationGuide?.next_action,
        latestProjectId,
        activeProjectType,
      }),
    });
  }

  for (const recommendation of personaRecommendations) {
    if (!inspirationsConfig.enabled && isInspirationRecommendation(recommendation)) {
      continue;
    }

    candidates.push({
      id: `persona-${recommendation.id}`,
      title: resolvePersonaRecommendationTitle(recommendation, t),
      description: resolvePersonaRecommendationDescription(recommendation, t),
      ctaLabel,
      action: {
        type: "navigate",
        path: resolvePersonaRecommendationActionPath(recommendation),
      },
    });
  }

  if (!hasPendingActivationSteps && projectsCount === 0 && !hasPendingActivationProjectStep) {
    candidates.push({
      id: "fallback-create-project",
      title: t("todayActionPlan.defaults.createProject.title", {
        defaultValue: "创建第一个项目",
      }),
      description: t("todayActionPlan.defaults.createProject.description", {
        defaultValue: "写一句灵感就能开始，大纲、正文和设定都会放在这个项目里。",
      }),
      ctaLabel: t("todayActionPlan.defaults.createProject.cta", {
        defaultValue: "新建项目",
      }),
      action: {
        type: "create_project",
        projectType: activeProjectType,
      },
    });
  } else if (!hasPendingActivationSteps && latestProjectId) {
    candidates.push({
      id: "fallback-open-project",
      title: t("todayActionPlan.defaults.openProject.title", {
        defaultValue: "继续最近的项目",
      }),
      description: t("todayActionPlan.defaults.openProject.description", {
        defaultValue: "从上次停下的地方接着写。",
      }),
      ctaLabel,
      action: {
        type: "navigate",
        path: `/project/${latestProjectId}`,
      },
    });
    candidates.push({
      id: "fallback-export-project",
      title: t("todayActionPlan.defaults.exportProject.title", {
        defaultValue: "导出你的作品",
      }),
      description: t("todayActionPlan.defaults.exportProject.description", {
        defaultValue: "打开项目，点「导出正文」下载成稿文件。",
      }),
      ctaLabel: t("todayActionPlan.defaults.exportProject.cta", {
        defaultValue: "打开项目",
      }),
      action: {
        type: "navigate",
        path: `/project/${latestProjectId}`,
      },
    });
  }

  candidates.push({
    id: "fallback-upgrade",
    title: t("todayActionPlan.defaults.upgrade.title", {
      defaultValue: "查看额度",
    }),
    description: t("todayActionPlan.defaults.upgrade.description", {
      defaultValue: "看看本月还剩多少额度，不够时可以升级。",
    }),
    ctaLabel: t("todayActionPlan.defaults.upgrade.cta", {
      defaultValue: "查看额度",
    }),
    action: {
      type: "navigate",
      path: buildUpgradeUrl("/dashboard/billing", "dashboard_today_action"),
    },
  });

  if (inspirationsConfig.enabled) {
    candidates.push({
      id: "fallback-inspirations",
      title: t("todayActionPlan.defaults.inspirations.title", {
        defaultValue: "逛逛灵感库",
      }),
      description: t("todayActionPlan.defaults.inspirations.description", {
        defaultValue: "卡住时，看看精选灵感找开头和设定。",
      }),
      ctaLabel,
      action: {
        type: "navigate",
        path: "/dashboard/inspirations",
      },
    });
  }

  const uniqueItems: TodayActionPlanItem[] = [];
  const seen = new Set<string>();
  for (const item of candidates) {
    const uniqueId = createActionId(item);
    if (seen.has(uniqueId)) {
      continue;
    }
    seen.add(uniqueId);
    uniqueItems.push(item);
  }

  return uniqueItems.slice(0, 3);
}

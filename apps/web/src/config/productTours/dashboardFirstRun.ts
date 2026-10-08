import { inspirationsConfig } from '../inspirations';

export type ProductTourPlacement = 'top' | 'right' | 'bottom' | 'left' | 'center';
export type ProductTourNextMode = 'manual' | 'target_click';
export type ProductTourMissingBehavior = 'wait' | 'skip' | 'abort';
export type ProductTourMobileBehavior = 'sheet' | 'tooltip';

export interface ProductTourStep {
  id: string;
  route: string;
  targetId: string;
  mobileTargetId?: string;
  mobileSpotlightPadding?: number;
  spotlightOffsetX?: number;
  spotlightOffsetY?: number;
  mobileSpotlightOffsetX?: number;
  mobileSpotlightOffsetY?: number;
  titleKey?: string;
  descriptionKey?: string;
  ctaLabelKey?: string;
  defaultTitle: string;
  defaultDescription: string;
  defaultCtaLabel: string;
  placement?: ProductTourPlacement;
  nextMode?: ProductTourNextMode;
  ifMissing?: ProductTourMissingBehavior;
  mobileBehavior?: ProductTourMobileBehavior;
  spotlightPadding?: number;
}

export interface ProductTourDefinition {
  id: string;
  version: number;
  steps: ProductTourStep[];
}

const dashboardSteps: ProductTourStep[] = [
    {
      id: 'project_type_tabs',
      route: '/dashboard',
      targetId: 'dashboard-project-type-tabs',
      titleKey: 'dashboardTour.steps.projectType.title',
      descriptionKey: 'dashboardTour.steps.projectType.description',
      ctaLabelKey: 'dashboardTour.steps.projectType.cta',
      defaultTitle: '先选你要写什么',
      defaultDescription: '长篇、短篇、短剧会走不同的创作路径，先选最接近你目标的一种。',
      defaultCtaLabel: '知道了',
      placement: 'bottom',
      nextMode: 'manual',
      ifMissing: 'wait',
      mobileBehavior: 'sheet',
      spotlightPadding: 8,
      mobileSpotlightPadding: 10,
      spotlightOffsetX: -10,
    },
    {
      id: 'inspiration_input',
      route: '/dashboard',
      targetId: 'dashboard-inspiration-input',
      titleKey: 'dashboardTour.steps.inspirationInput.title',
      descriptionKey: inspirationsConfig.enabled
        ? 'dashboardTour.steps.inspirationInput.description'
        : 'dashboardTour.steps.inspirationInput.descriptionWithoutLibrary',
      ctaLabelKey: 'dashboardTour.common.next',
      defaultTitle: '从一个想法开始',
      defaultDescription: inspirationsConfig.enabled
        ? '有个念头就写下来，一句话就够；也可以从下方灵感里挑一个。'
        : '有个念头就写下来，一句话就够；没想好也能直接点「开始创作」。',
      defaultCtaLabel: '下一步',
      placement: 'bottom',
      nextMode: 'manual',
      ifMissing: 'wait',
      mobileBehavior: 'sheet',
      spotlightPadding: 12,
      mobileSpotlightPadding: 14,
      spotlightOffsetX: -8,
    },
    {
      id: 'inspirations_link',
      route: '/dashboard',
      targetId: 'dashboard-inspirations-heading',
      titleKey: 'dashboardTour.steps.inspirationsLink.title',
      descriptionKey: 'dashboardTour.steps.inspirationsLink.description',
      ctaLabelKey: 'dashboardTour.common.next',
      defaultTitle: '没想法就先来这里',
      defaultDescription: '卡住时来这里找故事开头和设定灵感。',
      defaultCtaLabel: '下一步',
      placement: 'left',
      nextMode: 'manual',
      ifMissing: 'skip',
      mobileBehavior: 'sheet',
      spotlightPadding: 8,
      mobileSpotlightPadding: 18,
    },
    {
      id: 'create_project',
      route: '/dashboard',
      targetId: 'dashboard-create-project',
      titleKey: 'dashboardTour.steps.createProject.title',
      descriptionKey: 'dashboardTour.steps.createProject.description',
      ctaLabelKey: 'dashboardTour.steps.createProject.cta',
      defaultTitle: '一键创建项目',
      defaultDescription: '点这里后会立刻生成项目，你可以继续写作，或交给 AI 帮你扩写。',
      defaultCtaLabel: '去试试',
      placement: 'top',
      nextMode: 'target_click',
      ifMissing: 'wait',
      mobileBehavior: 'sheet',
      spotlightPadding: 6,
      mobileSpotlightPadding: 8,
      spotlightOffsetX: -6,
    },
];

export const DASHBOARD_FIRST_RUN_TOUR: ProductTourDefinition = {
  id: 'dashboard_first_run',
  version: 1,
  steps: dashboardSteps.filter((step) => inspirationsConfig.enabled || step.id !== 'inspirations_link'),
};

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BookOpen,
  Check,
  Clapperboard,
  FileText,
  Folder,
  FolderOpen,
  Loader2,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { Logo } from "../components/Logo";
import { cn } from "../lib/utils";
import { toast } from "../lib/toast";
import { trackEvent } from "../lib/analytics";
import { ApiError } from "../lib/apiClient";
import { getPreferredProjectType, setPreferredProjectType } from "../lib/preferredProjectType";
import { firstWorkApi, firstWorkQueryKey } from "../lib/firstWorkApi";
import { untouchedFirstWork } from "../hooks/useFirstWork";
import { useIsMobile } from "../hooks/useMediaQuery";
import { requestChatOnOpen } from "../lib/pendingInspiration";
import type { ProjectType } from "../types";
import {
  getPersonaOnboardingData,
  savePersonaOnboardingData,
  type PersonaExperienceLevel,
} from "../lib/onboardingPersona";
import { onboardingPersonaApi, personaOnboardingQueryKey } from "../lib/onboardingPersonaApi";
import { reportActivationEvent } from "../lib/activationEvents";

const MAX_PERSONA_SELECTION = 3;
const EXPERIENCE_LEVELS: PersonaExperienceLevel[] = ["beginner", "intermediate", "advanced"];
const DEFAULT_NEXT_PATH = "/dashboard";

interface TypeOption {
  type: ProjectType;
  /** The stored identity for this answer (the persona API is unchanged). */
  persona: string;
  icon: LucideIcon;
}

/**
 * One question, one tap: what the author wants to write. Each card names the platforms the
 * format suits and what the AI writes first; a new author's tap opens their first work.
 * See feature/2026-10-19-persona-one-question.md and
 * feature/2026-11-09-persona-opens-first-work.md.
 */
const TYPE_OPTIONS: TypeOption[] = [
  { type: "novel", persona: "serial", icon: BookOpen },
  { type: "short", persona: "short_story", icon: FileText },
  { type: "screenplay", persona: "screenwriter", icon: Clapperboard },
];
const TYPE_PERSONAS: ReadonlySet<string> = new Set(TYPE_OPTIONS.map((option) => option.persona));

/**
 * Identities added after the original five. An older server (the API deploys
 * after the web app) rejects them with a validation error, so the save is
 * retried once without them.
 */
const NEWER_PERSONA_IDS: ReadonlySet<string> = new Set(["short_story", "screenwriter"]);

const isValidationRejection = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 400 || error.status === 422);

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
    return DEFAULT_NEXT_PATH;
  }

  return `${pathname}${from?.search ?? ""}${from?.hash ?? ""}`;
};

interface StoredAnswers {
  personas: string[];
  goals: string[];
  experienceLevel: PersonaExperienceLevel | null;
}

const asStringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

/** Script lines keep their format: 【scene】, △ action, 人物：台词. */
function SampleLine({ line, script }: { line: string; script: boolean }) {
  if (script && line.startsWith("【")) {
    return <p className="font-sans text-[13px] font-semibold">{line}</p>;
  }
  if (script && line.startsWith("△")) {
    return <p className="text-[hsl(var(--text-secondary))]">{line}</p>;
  }
  const colon = script ? line.indexOf("：") : -1;
  if (colon > 0 && colon < 8) {
    return (
      <p>
        <span className="font-semibold">{line.slice(0, colon)}</span>
        {line.slice(colon)}
      </p>
    );
  }
  return <p>{line}</p>;
}

export default function OnboardingPersonaPage() {
  const { t, i18n } = useTranslation(["onboarding", "common"]);
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const isMobile = useIsMobile();
  const locationState = location.state as OnboardingLocationState | null;
  const nextPath = sanitizeNextPath(locationState?.from);

  const existingData = useMemo(
    () => (user ? getPersonaOnboardingData(user.id) : null),
    [user]
  );
  const personaQueryKey = personaOnboardingQueryKey(user?.id ?? "anonymous");
  const { data: serverState } = useQuery({
    queryKey: personaQueryKey,
    queryFn: onboardingPersonaApi.getState,
    enabled: Boolean(user),
  });

  // Earlier answers (server truth first, then this browser's copy). Identities and goals the
  // page no longer asks about are kept when the author answers again.
  const stored: StoredAnswers | null = useMemo(() => {
    const profile = serverState?.profile ?? existingData;
    if (!profile) return null;
    return {
      personas: profile.selected_personas ?? [],
      goals: profile.selected_goals ?? [],
      experienceLevel: EXPERIENCE_LEVELS.includes(profile.experience_level) ? profile.experience_level : null,
    };
  }, [existingData, serverState]);
  const previousType = stored
    ? TYPE_OPTIONS.find((option) => stored.personas.includes(option.persona))?.type ?? null
    : null;
  // A returning author sees their last answer; a new one sees the type chosen on the landing page.
  const highlightedType = previousType ?? (user ? getPreferredProjectType(user.id) : null);

  // Desktop preview follows hover and focus; it starts on the highlighted type.
  const [previewType, setPreviewType] = useState<ProjectType>(highlightedType ?? "novel");

  const mountedRef = useRef(false);
  const [saving, setSaving] = useState<ProjectType | "skip" | null>(null);
  // Synchronous guard: a double tap lands before `saving` re-renders.
  const savingRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // Keep this browser's copy in step with the server (other pages read it).
  useEffect(() => {
    if (!user || !serverState?.profile) return;
    const profile = serverState.profile;
    savePersonaOnboardingData(user.id, {
      selected_personas: profile.selected_personas ?? [],
      selected_goals: profile.selected_goals ?? [],
      experience_level: EXPERIENCE_LEVELS.includes(profile.experience_level) ? profile.experience_level : "beginner",
      skipped: Boolean(profile.skipped),
    });
  }, [serverState, user]);

  // New-user funnel: the question was shown (once per mount).
  const viewTrackedRef = useRef(false);
  useEffect(() => {
    if (viewTrackedRef.current) return;
    viewTrackedRef.current = true;
    trackEvent("persona_viewed");
  }, []);

  /**
   * A chosen type on the default path opens the author's first work. The server decides
   * eligibility and is idempotent; anything other than an untouched first work (older
   * accounts, rollout off, a failed request) keeps the original destination.
   */
  const openFirstWork = async (userId: string, type: ProjectType): Promise<boolean> => {
    if (nextPath !== DEFAULT_NEXT_PATH) return false;
    try {
      const state = await firstWorkApi.initialize(type, i18n.language);
      if (!mountedRef.current) return true;
      queryClient.setQueryData(firstWorkQueryKey(userId), state);
      const work = untouchedFirstWork(state);
      if (!work) return false;
      // Phones open on the AI tab, where the starter cards are; desktop shows both panes.
      if (isMobile) requestChatOnOpen(work.projectId);
      navigate(`/project/${work.projectId}?file=${work.fileId}`, { replace: true });
      return true;
    } catch {
      return false;
    }
  };

  const handleChoose = async (choice: TypeOption | null) => {
    if (!user || savingRef.current) return;
    const skip = choice === null;

    savingRef.current = true;
    setSaving(choice?.type ?? "skip");
    const keptPersonas = (stored?.personas ?? []).filter((id) => !TYPE_PERSONAS.has(id));
    const payload = {
      selected_personas: skip ? [] : [choice.persona, ...keptPersonas].slice(0, MAX_PERSONA_SELECTION),
      selected_goals: skip ? [] : stored?.goals ?? [],
      // Unanswered experience keeps the server default instead of a value the author never picked.
      ...(stored?.experienceLevel ? { experience_level: stored.experienceLevel } : {}),
      skipped: skip,
    };

    const saveWithOlderServerFallback = async () => {
      try {
        return await onboardingPersonaApi.save(payload);
      } catch (error) {
        const olderPersonas = payload.selected_personas.filter((id) => !NEWER_PERSONA_IDS.has(id));
        if (!isValidationRejection(error) || olderPersonas.length === payload.selected_personas.length) {
          throw error;
        }
        // An older server does not know the new identities. Keep everything
        // else; with nothing left, record the answers as skipped so the author
        // is not stuck on this page.
        return onboardingPersonaApi.save({
          ...payload,
          selected_personas: olderPersonas,
          skipped: payload.skipped || olderPersonas.length === 0,
        });
      }
    };

    try {
      await queryClient.cancelQueries({ queryKey: personaQueryKey });
      const result = await saveWithOlderServerFallback();
      if (!mountedRef.current) return;
      const profile = result.profile;
      if (!profile) {
        throw new Error("Persona onboarding save returned no profile");
      }

      // The answer to "what do you want to write" is the author's latest explicit type choice.
      if (choice) setPreferredProjectType(choice.type, user.id);

      savePersonaOnboardingData(user.id, {
        selected_personas: profile.selected_personas,
        selected_goals: profile.selected_goals,
        // The server fills its default when the question was left unanswered.
        experience_level: profile.experience_level ?? "beginner",
        skipped: profile.skipped,
      });
      queryClient.setQueryData(personaQueryKey, result);
      trackEvent("persona_submitted", { skipped: profile.skipped, project_type: choice?.type });
      // New-user funnel (server side, once per account): the persona screen was answered or skipped.
      void reportActivationEvent(user.id, { event_name: "persona_submitted", skipped: profile.skipped });
      // 「先随便看看」 means looking around: skipping keeps the dashboard, where the first work also waits.
      if (choice && await openFirstWork(user.id, choice.type)) return;
      if (!mountedRef.current) return;
      navigate(nextPath, { replace: true });
    } catch {
      if (mountedRef.current) {
        toast.error(t("onboarding:errors.saveFailed", "保存失败，请检查网络后重试"));
      }
    } finally {
      savingRef.current = false;
      if (mountedRef.current) setSaving(null);
    }
  };

  if (!user) return null;

  const option = (type: ProjectType, key: string) => t(`onboarding:start.options.${type}.${key}`);
  const optionList = (type: ProjectType, key: string) =>
    asStringList(t(`onboarding:start.options.${type}.${key}`, { returnObjects: true }));
  const previewFolders = optionList(previewType, "folders");
  const previewScript = previewType === "screenplay";
  // Chapter and episode samples open with a heading; a short story starts straight in.
  const previewTitle = t(`onboarding:start.options.${previewType}.sampleTitle`, "");

  return (
    <div className="ui-sans-headings min-h-screen bg-[hsl(var(--bg-primary))]">
      <div className="mx-auto max-w-xl px-4 pb-8 pt-4 sm:px-6 sm:pt-8 lg:max-w-[1080px]">
        <Logo className="h-6 w-auto text-[hsl(var(--text-primary))]" />

        <h1 className="mt-6 text-2xl font-bold text-[hsl(var(--text-primary))] sm:mt-9 sm:text-3xl">
          {t("onboarding:start.title", "你准备写哪种作品？")}
        </h1>
        {previousType && (
          <p className="mt-3 inline-flex items-center gap-1.5 text-xs text-[hsl(var(--accent-primary))]">
            <Check className="h-3.5 w-3.5" />
            {t("onboarding:hero.restore", "已带入你上次的选择")}
          </p>
        )}

        <div className="mt-5 grid items-start gap-7 sm:mt-7 lg:grid-cols-[minmax(0,1fr)_440px]">
          <ul className="grid gap-3" aria-label={t("onboarding:start.title", "你准备写哪种作品？")}>
            {TYPE_OPTIONS.map(({ type, icon: Icon }) => {
              const highlighted = highlightedType === type;
              const loading = saving === type;
              return (
                <li key={type}>
                  <button
                    type="button"
                    data-testid={`onboarding-type-${type}`}
                    onClick={() => { void handleChoose(TYPE_OPTIONS.find((item) => item.type === type) ?? null); }}
                    onMouseEnter={() => setPreviewType(type)}
                    onFocus={() => setPreviewType(type)}
                    disabled={saving !== null}
                    aria-pressed={highlighted}
                    className={cn(
                      "group grid w-full grid-cols-[36px_minmax(0,1fr)] items-center gap-x-3 gap-y-2 rounded-2xl border px-4 pt-3.5 text-left transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)] disabled:cursor-default",
                      "sm:grid-cols-[40px_minmax(0,1fr)_auto] sm:gap-x-4 sm:px-5 sm:py-4",
                      saving !== null && !loading && "opacity-50",
                      "border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.55)]",
                      // One marked card at a time: the earlier answer on narrow screens, the previewed
                      // type where the preview is shown (it starts on the earlier answer).
                      highlighted && "max-lg:border-[hsl(var(--accent-primary)/0.55)] max-lg:bg-[hsl(var(--accent-primary)/0.04)]",
                      previewType === type && "lg:border-[hsl(var(--accent-primary)/0.55)] lg:bg-[hsl(var(--accent-primary)/0.04)]"
                    )}
                  >
                    <span className="flex h-9 w-9 items-center justify-center self-start rounded-xl bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))] sm:h-10 sm:w-10">
                      <Icon aria-hidden="true" className="h-[18px] w-[18px] sm:h-5 sm:w-5" />
                    </span>
                    <span className="contents sm:grid sm:min-w-0 sm:gap-2">
                      <span className="text-base font-semibold text-[hsl(var(--text-primary))] sm:text-[17px]">
                        {option(type, "title")}
                      </span>
                      <span className="col-span-2 flex flex-wrap items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))] sm:col-span-1">
                        <span className="mr-0.5">{t("onboarding:start.fit", "适合")}</span>
                        {optionList(type, "platforms").map((platform) => (
                          <span
                            key={platform}
                            className="whitespace-nowrap rounded-md bg-[hsl(var(--bg-tertiary))] px-2 py-0.5 text-[hsl(var(--text-primary)/0.82)]"
                          >
                            {platform}
                          </span>
                        ))}
                      </span>
                      {/* Phones have no preview: the folders the work opens with stay on the card. */}
                      <span className="col-span-2 flex items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))] lg:hidden">
                        <Folder aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
                        {optionList(type, "folders").join(" / ")}
                      </span>
                    </span>
                    <span className="col-span-2 flex min-h-[44px] items-center justify-between gap-1.5 whitespace-nowrap border-t border-[hsl(var(--separator-color))] text-sm font-medium text-[hsl(var(--accent-primary))] sm:col-span-1 sm:min-h-0 sm:border-t-0">
                      {option(type, "promise")}
                      {loading ? (
                        <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                      ) : (
                        <ArrowRight aria-hidden="true" className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          {/* Desktop only, purely supplementary: what the work looks like once opened. */}
          <aside
            aria-hidden="true"
            className="sticky top-6 hidden overflow-hidden rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] lg:block"
            data-testid="onboarding-preview"
          >
            <div className="flex items-center justify-between gap-3 border-b border-[hsl(var(--separator-color))] px-4 py-3">
              <span className="text-sm font-semibold text-[hsl(var(--text-primary))]">{option(previewType, "title")}</span>
              <span className="text-xs text-[hsl(var(--text-secondary))]">{t("onboarding:start.preview", "示例")}</span>
            </div>
            <div className="grid min-h-[336px] grid-cols-[144px_minmax(0,1fr)]">
              <ul className="border-r border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-primary))] p-2 text-[13px]">
                {previewFolders.map((folder, index) => {
                  const last = index === previewFolders.length - 1;
                  const FolderIcon = last ? FolderOpen : Folder;
                  return (
                    <li key={folder} className="flex min-h-[30px] items-center gap-1.5 whitespace-nowrap px-2 text-[hsl(var(--text-primary))]">
                      <FolderIcon className="h-[15px] w-[15px] shrink-0 text-[hsl(var(--text-secondary))]" />
                      {folder}
                    </li>
                  );
                })}
                <li className="flex min-h-[30px] items-center gap-1.5 whitespace-nowrap rounded-lg bg-[hsl(var(--accent-primary)/0.1)] pl-6 pr-2 font-medium text-[hsl(var(--accent-dark))]">
                  <FileText className="h-[15px] w-[15px] shrink-0 text-[hsl(var(--accent-primary))]" />
                  {option(previewType, "file")}
                </li>
              </ul>
              <div className="px-[18px] pb-4 pt-4 text-sm leading-[1.85] text-[hsl(var(--text-primary))] [font-family:var(--font-body)] [&>p]:mb-1.5">
                {previewTitle && <p className="mb-2.5 text-[17px] font-semibold">{previewTitle}</p>}
                {optionList(previewType, "sample").map((line, index) => (
                  <SampleLine key={index} line={line} script={previewScript} />
                ))}
              </div>
            </div>
          </aside>
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[13px] text-[hsl(var(--text-secondary))]">
          <p>{t("onboarding:start.switchLater", "选了之后也随时能换。")}</p>
          <button
            type="button"
            onClick={() => { void handleChoose(null); }}
            disabled={saving !== null}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-md px-1 text-sm underline decoration-[hsl(var(--text-secondary)/0.35)] underline-offset-4 hover:text-[hsl(var(--text-primary))] hover:decoration-current disabled:opacity-50"
          >
            {saving === "skip" && <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />}
            {t("onboarding:actions.skip", "还没定？先随便看看")}
          </button>
        </div>
        <p className="sr-only" role="status" aria-live="polite">
          {saving !== null && saving !== "skip" ? t("onboarding:start.opening", "正在打开你的作品…") : ""}
        </p>
      </div>
    </div>
  );
}

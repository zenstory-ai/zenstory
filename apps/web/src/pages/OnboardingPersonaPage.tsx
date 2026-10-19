import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Check, ChevronRight, Clapperboard, FileText, Loader2, type LucideIcon } from "lucide-react";
import { Button } from "../components/ui/Button";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "../lib/utils";
import { toast } from "../lib/toast";
import { trackEvent } from "../lib/analytics";
import { ApiError } from "../lib/apiClient";
import { getPreferredProjectType, setPreferredProjectType } from "../lib/preferredProjectType";
import type { ProjectType } from "../types";
import {
  getPersonaOnboardingData,
  savePersonaOnboardingData,
  type PersonaExperienceLevel,
} from "../lib/onboardingPersona";
import { onboardingPersonaApi, personaOnboardingQueryKey } from "../lib/onboardingPersonaApi";

const MAX_PERSONA_SELECTION = 3;
const EXPERIENCE_LEVELS: PersonaExperienceLevel[] = ["beginner", "intermediate", "advanced"];

interface TypeOption {
  type: ProjectType;
  /** The stored identity for this answer (the persona API is unchanged). */
  persona: string;
  icon: LucideIcon;
}

/**
 * One question, one tap: what the author wants to write. The answer is stored as the
 * matching identity and opens the dashboard on that type.
 * See feature/2026-10-19-persona-one-question.md.
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
    return "/dashboard";
  }

  return `${pathname}${from?.search ?? ""}${from?.hash ?? ""}`;
};

interface StoredAnswers {
  personas: string[];
  goals: string[];
  experienceLevel: PersonaExperienceLevel | null;
}

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

  return (
    <div className="ui-sans-headings min-h-screen bg-[hsl(var(--bg-primary))]">
      <div className="mx-auto max-w-xl px-4 py-8 sm:px-6 sm:py-14">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold text-[hsl(var(--text-primary))]">
              {t("onboarding:start.title", "你想写什么？")}
            </h1>
            <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
              {t("onboarding:start.subtitle", "选一个，工作台会先打开这一类，之后随时能换。")}
            </p>
          </div>
          <Button
            variant="ghost"
            size="touch"
            className="shrink-0"
            onClick={() => { void handleChoose(null); }}
            disabled={saving !== null}
            isLoading={saving === "skip"}
          >
            {t("onboarding:actions.skip", "先跳过")}
          </Button>
        </div>

        {previousType && (
          <p className="mt-3 inline-flex items-center gap-1.5 text-xs text-[hsl(var(--accent-primary))]">
            <Check className="h-3.5 w-3.5" />
            {t("onboarding:hero.restore", "已带入你上次的选择")}
          </p>
        )}

        <div className="mt-6 grid gap-3">
          {TYPE_OPTIONS.map((option) => {
            const Icon = option.icon;
            const highlighted = highlightedType === option.type;
            return (
              <button
                key={option.type}
                type="button"
                data-testid={`onboarding-type-${option.type}`}
                onClick={() => { void handleChoose(option); }}
                disabled={saving !== null}
                aria-pressed={highlighted}
                className={cn(
                  "flex min-h-[64px] items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)] disabled:cursor-not-allowed",
                  highlighted
                    ? "border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.08)]"
                    : "border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.55)]"
                )}
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]">
                  {saving === option.type ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-[hsl(var(--text-primary))]">
                    {t(`onboarding:start.options.${option.type}.title`, option.type)}
                  </span>
                  <span className="mt-0.5 block text-xs text-[hsl(var(--text-secondary))]">
                    {t(`onboarding:start.options.${option.type}.desc`, "")}
                  </span>
                </span>
                {/* One tap goes straight in. */}
                <ChevronRight aria-hidden="true" className="h-4 w-4 shrink-0 text-[hsl(var(--text-secondary))]" />
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

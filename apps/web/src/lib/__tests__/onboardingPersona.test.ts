import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getPersonaOnboardingData,
  PERSONA_ONBOARDING_STORAGE_KEY_PREFIX,
  savePersonaOnboardingData,
} from "../onboardingPersona";

describe("onboardingPersona storage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("treats malformed saved payload as null", () => {
    localStorage.setItem(
      `${PERSONA_ONBOARDING_STORAGE_KEY_PREFIX}:user-malformed`,
      JSON.stringify({
        version: 1,
        completed_at: "2026-03-06T09:00:00Z",
        selected_personas: ["explorer"],
        selected_goals: "finishBook",
        experience_level: "beginner",
        skipped: false,
      })
    );

    expect(getPersonaOnboardingData("user-malformed")).toBeNull();
  });

  it("saves onboarding payload with stable schema fields", () => {
    const saved = savePersonaOnboardingData("user-save", {
      selected_personas: ["builder", "strategist"],
      selected_goals: ["publishFast"],
      experience_level: "advanced",
      skipped: true,
    });

    expect(saved).not.toBeNull();
    expect(saved?.version).toBe(1);
    expect(typeof saved?.completed_at).toBe("string");
    expect(saved?.selected_personas).toEqual(["builder", "strategist"]);
    expect(saved?.selected_goals).toEqual(["publishFast"]);
    expect(saved?.experience_level).toBe("advanced");
    expect(saved?.skipped).toBe(true);
    expect(getPersonaOnboardingData("user-save")).toEqual(saved);
  });

  it("returns null when saving or reading with empty user id", () => {
    const saved = savePersonaOnboardingData("", {
      selected_personas: ["explorer"],
      selected_goals: [],
      experience_level: "beginner",
      skipped: true,
    });

    expect(saved).toBeNull();
    expect(getPersonaOnboardingData("")).toBeNull();
  });
});

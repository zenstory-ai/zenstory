import { getApiBase, getCurrentRequestLanguage, readApiError } from "../apiClient";
import type { Acquisition } from "../acquisition";

/**
 * Authentication API endpoints.
 *
 * Handles user registration, login, OAuth flows, and email verification.
 * These endpoints do not require authentication (except token refresh).
 */
export const authApi = {
  correctRegistrationEmail: async (data: {
    email: string;
    password: string;
    new_email: string;
    language: "zh" | "en";
  }) => {
    const response = await fetch(`${getApiBase()}/api/auth/registration-email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw await readApiError(response);
    }
    return response.json() as Promise<{ email: string; verification_sent: boolean }>;
  },

  register: async (data: {
    username?: string;
    email: string;
    password: string;
    language?: "zh" | "en";
    acquisition?: Acquisition;
  }) => {
    const response = await fetch(`${getApiBase()}/api/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw await readApiError(response);
    }
    return response.json();
  },

  login: async (username: string, password: string) => {
    const formData = new FormData();
    formData.append("username", username);
    formData.append("password", password);
    const response = await fetch(`${getApiBase()}/api/auth/login`, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw await readApiError(response);
    }
    return response.json();
  },

  verifyEmail: async (email: string, code: string) => {
    const response = await fetch(`${getApiBase()}/api/auth/verify-email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code }),
    });
    if (!response.ok) {
      throw await readApiError(response);
    }
    return response.json();
  },

  resendVerification: async (email: string, language?: "zh" | "en") => {
    const resolvedLanguage = language ?? getCurrentRequestLanguage();
    const response = await fetch(`${getApiBase()}/api/auth/resend-verification`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept-Language": resolvedLanguage,
      },
      body: JSON.stringify({ email, language: resolvedLanguage }),
    });
    if (!response.ok) {
      throw await readApiError(response);
    }
    return response.json();
  },

  checkVerification: async (email: string) => {
    const response = await fetch(
      `${getApiBase()}/api/auth/check-verification?email=${encodeURIComponent(email)}`,
    );
    if (!response.ok) {
      throw await readApiError(response);
    }
    return response.json();
  },
};

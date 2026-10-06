import React, { useState } from "react";
import { useNavigate, useLocation, Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LogoMark } from "../components/Logo";
import { useAuth } from "../contexts/AuthContext";
import { projectApi } from "../lib/api";
import { authConfig, hasOAuthProviders } from "../config/auth";
import { PublicHeader } from "../components/PublicHeader";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { normalizePlanIntent } from "../lib/authFlow";
import { handleSsoRedirect } from '../lib/ssoRedirect';

// SVG icons for OAuth providers
const GoogleIcon = () => (
  <svg className="w-5 h-5" viewBox="0 0 24 24">
    <path
      fill="currentColor"
      d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
    />
    <path
      fill="currentColor"
      d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
    />
    <path
      fill="currentColor"
      d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
    />
    <path
      fill="currentColor"
      d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
    />
  </svg>
);

const AppleIcon = () => (
  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
    <path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.54 4.09l.01-.01zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.29 2.58-2.34 4.5-3.74 4.25z" />
  </svg>
);

export const Login: React.FC = () => {
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login, googleLogin, appleLogin } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation(['auth', 'common', 'privacy', 'home']);
  const [searchParams] = useSearchParams();
  const planIntent = normalizePlanIntent(searchParams.get('plan'));
  const hasPaidPlanIntent = planIntent !== null && planIntent !== 'free';
  const registerLink = planIntent
    ? `/register?plan=${encodeURIComponent(planIntent)}`
    : "/register";
  const selectedPlanLabel = planIntent
    ? t(`auth:plans.${planIntent}`, planIntent.toUpperCase())
    : '';
  const trimmedIdentifier = identifier.trim();
  const canSubmit = trimmedIdentifier.length > 0 && password.length > 0 && !loading;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!canSubmit) {
      setError(t("auth:errors.missingCredentials", "请输入账号和密码"));
      return;
    }

    setLoading(true);

    try {
      await login(trimmedIdentifier, password);

      // Restore deep-link intent (e.g. homepage CTA → dashboard settings)
      const state = location.state as Record<string, unknown> | null;
      const from = state?.from as { pathname?: string; search?: string; hash?: string; state?: object } | undefined;
      if (from && typeof from.pathname === 'string') {
        navigate(`${from.pathname}${from.search ?? ''}${from.hash ?? ''}`, { replace: true, state: from.state ?? {} });
        return;
      }

      // Check for redirect parameter from external apps
      const params = new URLSearchParams(window.location.search);
      const redirectUrl = params.get('redirect');

      if (redirectUrl) {
        // Use validated SSO redirect handler (token is fresh from login)
        const result = await handleSsoRedirect(redirectUrl);
        if (result.success && result.redirectUrl) {
          window.location.href = result.redirectUrl;
          return;
        }

        setError(t('auth:errors.ssoRedirectFailed', '已登录，但没能返回原来的应用，请重新打开该应用再试'));
        return;
      }

      if (hasPaidPlanIntent && planIntent) {
        navigate(`/dashboard/billing?plan=${encodeURIComponent(planIntent)}`);
        return;
      }

      // Check if user has existing projects
      try {
        const projects = await projectApi.getAll();
        if (projects.length > 0) {
          const STORAGE_KEY_PREFIX = 'zenstory_current_project_id';

          // Prefer last used project for this user.
          const rawUser = localStorage.getItem('user');
          let userId: string | undefined;
          try {
            userId = rawUser ? (JSON.parse(rawUser) as { id?: string }).id : undefined;
          } catch {
            userId = undefined;
          }
          const storageKey = userId ? `${STORAGE_KEY_PREFIX}:${userId}` : STORAGE_KEY_PREFIX;

          const savedProjectId =
            localStorage.getItem(storageKey) ?? localStorage.getItem(STORAGE_KEY_PREFIX);

          if (savedProjectId && projects.some(p => p.id === savedProjectId)) {
            navigate(`/project/${savedProjectId}`);
            return;
          }

          // Fallback: navigate to most recently updated (or created) project.
          const toMillis = (ts?: string) => {
            const n = ts ? new Date(ts).getTime() : 0;
            return Number.isFinite(n) ? n : 0;
          };

          const sortedProjects = [...projects].sort((a, b) => {
            const aTs = Math.max(toMillis(a.updated_at), toMillis(a.created_at));
            const bTs = Math.max(toMillis(b.updated_at), toMillis(b.created_at));
            return bTs - aTs;
          });

          navigate(`/project/${sortedProjects[0].id}`);
        } else {
          // No projects, go to dashboard
          navigate("/dashboard");
        }
      } catch {
        // If fetching projects fails, just go to dashboard
        navigate("/dashboard");
      }
    } catch (err: unknown) {
      const error = err as { message?: string };
      setError(error.message || t('auth:errors.invalidCredentials'));
    } finally {
      setLoading(false);
    }
  };

  const handleOAuthLogin = (provider: 'google' | 'apple') => {
    setError("");
    if (provider === 'google') {
      googleLogin({ planIntent });
    } else if (provider === 'apple') {
      appleLogin();
    }
  };

  const showOAuthDivider = hasOAuthProviders();

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col">
      {/* Header */}
      <PublicHeader variant="auth" maxWidth="max-w-6xl" />

      <div className="flex-1 flex items-center justify-center p-4 sm:p-6">
        <div className="w-full max-w-md">
          {/* Logo */}
          <div className="text-center mb-8 sm:mb-10">
            <div className="inline-flex items-center gap-2.5 mb-4">
              <div className="w-12 h-12 rounded-xl bg-[hsl(var(--accent-primary))] flex items-center justify-center shadow-lg">
                <LogoMark className="w-7 h-7 text-white" />
              </div>
            </div>
            <h1 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-2">{t('auth:login.title')}</h1>
            <p className="text-[hsl(var(--text-secondary))] text-sm">{t('auth:login.subtitle')}</p>
          </div>

        {/* Login Form */}
        <div className="bg-[hsl(var(--bg-secondary))] rounded-2xl p-6 sm:p-8 shadow-lg border border-[hsl(var(--border-color))]">
          {error && (
            <div
              id="login-error"
              role="alert"
              aria-live="assertive"
              className="bg-[hsl(var(--error)/0.1)] border border-[hsl(var(--error)/0.3)] rounded-xl p-4 mb-6"
            >
              <p className="text-[hsl(var(--error))] text-sm">{error}</p>
            </div>
          )}
          {hasPaidPlanIntent && (
            <div className="bg-[hsl(var(--accent-primary)/0.08)] border border-[hsl(var(--accent-primary)/0.22)] rounded-xl p-4 mb-6">
              <p className="text-[hsl(var(--text-primary))] text-sm font-medium">
                {t('auth:login.planIntentTitle', { plan: selectedPlanLabel })}
              </p>
              <p className="text-[hsl(var(--text-secondary))] text-xs mt-1">
                {t('auth:login.planIntentSubtitle')}
              </p>
            </div>
          )}

          {/* OAuth Buttons */}
          {showOAuthDivider && (
            <>
              <div className="space-y-3 mb-6">
                {authConfig.oauthProviders.google.enabled && (
                  <button
                    type="button"
                    onClick={() => handleOAuthLogin('google')}
                    className="w-full flex items-center justify-center gap-3 bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--bg-card))] text-[hsl(var(--text-primary))] font-medium py-3 rounded-xl border border-[hsl(var(--border-color))] transition-colors"
                  >
                    <GoogleIcon />
                    {t('auth:login.googleLogin')}
                  </button>
                )}
                {authConfig.oauthProviders.apple.enabled && (
                  <button
                    type="button"
                    onClick={() => handleOAuthLogin('apple')}
                    className="w-full flex items-center justify-center gap-3 bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--bg-card))] text-[hsl(var(--text-primary))] font-medium py-3 rounded-xl border border-[hsl(var(--border-color))] transition-colors"
                  >
                    <AppleIcon />
                    {t('auth:login.appleLogin')}
                  </button>
                )}
              </div>

              {/* Divider */}
              <div className="relative mb-6">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-[hsl(var(--border-color))]"></div>
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="px-3 bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))]">{t('auth:login.or')}</span>
                </div>
              </div>
            </>
          )}

          {/* data-testid: login-form - Login form container for authentication tests */}
          <form onSubmit={handleSubmit} data-testid="login-form" aria-busy={loading}>
            {/* Username/Email */}
            <div className="mb-4">
              <label
                htmlFor="identifier"
                className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
              >
                {t('auth:login.usernameLabel')}
              </label>
              {/* data-testid: email-input - Email/username input field for login validation */}
              <input
                id="identifier"
                type="text"
                value={identifier}
                onChange={(e) => {
                  setIdentifier(e.target.value);
                  if (error) setError("");
                }}
                className="input"
                placeholder={t('auth:login.usernamePlaceholder')}
                required
                autoFocus
                data-testid="email-input"
                autoComplete="username"
                disabled={loading}
                aria-invalid={Boolean(error)}
                aria-describedby={error ? "login-error" : undefined}
              />
            </div>

            {/* Password */}
            <div className="mb-4">
              <div className="flex items-center justify-between mb-2">
                <label
                  htmlFor="password"
                  className="block text-[hsl(var(--text-secondary))] text-sm font-medium"
                  >
                  {t('auth:login.passwordLabel')}
                </label>
                {authConfig.forgotPasswordEnabled && (
                  <Link
                    to="/forgot-password"
                    className="text-sm text-[hsl(var(--accent-primary))] hover:text-[hsl(var(--accent-light))] transition-colors font-medium"
                  >
                    {t('auth:login.forgotPassword')}
                  </Link>
                )}
              </div>
              {/* data-testid: password-input - Password input field for login validation */}
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  if (error) setError("");
                }}
                className="input"
                placeholder={t('auth:login.passwordPlaceholder')}
                required
                data-testid="password-input"
                autoComplete="current-password"
                disabled={loading}
                aria-invalid={Boolean(error)}
                aria-describedby={error ? "login-error" : undefined}
              />
            </div>

            {/* Submit Button */}
            {/* data-testid: login-submit - Submit button for login action */}
            <button
              type="submit"
              disabled={!canSubmit}
              className="btn-primary w-full py-3 text-base"
              data-testid="login-submit"
              aria-busy={loading}
            >
              {loading ? (
                <LoadingSpinner
                  size="sm"
                  variant="css"
                  color="white"
                  label={t('auth:login.submitting')}
                />
              ) : (
                t('auth:login.submit')
              )}
            </button>
          </form>

          {/* Register Link */}
          <div className="mt-6 text-center">
            {authConfig.registrationEnabled && (
              <p className="text-[hsl(var(--text-secondary))] text-sm">
                {t('auth:login.noAccount')}{" "}
                <Link
                  to={registerLink}
                  className="text-[hsl(var(--accent-primary))] hover:text-[hsl(var(--accent-light))] font-medium transition-colors"
                >
                  {t('auth:login.register')}
                </Link>
              </p>
            )}
            <p className="mt-3 text-xs text-[hsl(var(--text-secondary))] opacity-80">
              <Link to="/terms-of-service" target="_blank" rel="noopener noreferrer" className="hover:opacity-100 transition-opacity">
                {t('privacy:footer.terms')}
              </Link>
              {" · "}
              <Link to="/privacy-policy" target="_blank" rel="noopener noreferrer" className="hover:opacity-100 transition-opacity">
                {t('privacy:footer.privacy')}
              </Link>
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="mt-8 text-center">
          <p className="text-[hsl(var(--text-secondary))] text-xs opacity-60">
            {t('home:footer.copyright')}
          </p>
        </div>
        </div>
      </div>
    </div>
  );
}

export default Login;

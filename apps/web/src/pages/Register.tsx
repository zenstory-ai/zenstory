import React, { useState, useEffect, useRef } from "react";
import { useNavigate, Link, Navigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LogoMark } from "../components/Logo";
import { useAuth } from "../contexts/AuthContext";
import { authConfig, hasOAuthProviders } from "../config/auth";
import { PublicHeader } from "../components/PublicHeader";
import { Button } from "../components/ui/Button";
import { translateError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { ApiError } from "../lib/apiClient";
import { normalizePlanIntent, type PlanIntent } from "../lib/authFlow";
import { Eye, EyeOff } from "lucide-react";
import { EmailSuggestionInput } from "../components/EmailSuggestionInput";

const MAX_PASSWORD_BYTES = 72;

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

export const Register: React.FC = () => {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState<PlanIntent | null>(null);
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [formError, setFormError] = useState("");
  // The address the server reported as already registered. The same answer
  // covers verified and unverified accounts, so both exits are offered.
  const [existingEmail, setExistingEmail] = useState<string | null>(null);
  const { register, googleLogin } = useAuth();
  const navigate = useNavigate();
  const { t } = useTranslation(['auth', 'common', 'home']);
  const [searchParams] = useSearchParams();
  const submitInFlightRef = useRef(false);

  // Read plan intent and error code from URL parameters
  useEffect(() => {
    setSelectedPlan(normalizePlanIntent(searchParams.get('plan')));

    const errorCode = searchParams.get('error_code');
    if (errorCode) {
      const message = translateError(errorCode);
      setFormError(message);
      toast.error(message);
    }
  }, [searchParams]);

  const hasPaidPlanIntent = selectedPlan !== null && selectedPlan !== 'free';
  const selectedPlanLabel = selectedPlan
    ? t(`auth:plans.${selectedPlan}`, selectedPlan.toUpperCase())
    : '';
  const loginLink = selectedPlan
    ? `/login?plan=${encodeURIComponent(selectedPlan)}`
    : "/login";
  const verifyEmailLink = selectedPlan
    ? `/verify-email?plan=${encodeURIComponent(selectedPlan)}`
    : "/verify-email";
  const trimmedEmail = email.trim();
  const isEmailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail);
  const isPasswordLengthValid = password.length >= 6;
  // The submit button is only disabled while a request is running or after
  // success. Incomplete forms stay clickable so every click gets a visible
  // answer (inline message + toast) instead of a silent no-op.
  const submitDisabled = loading || success;

  // Coming from a card halfway down the landing page, the browser keeps the
  // old scroll offset and cuts off the title; start the form at the top.
  useEffect(() => {
    try {
      window.scrollTo({ top: 0 });
    } catch {
      // Non-browser environments without scrollTo.
    }
  }, []);

  const rejectSubmit = (message: string, fieldId?: string) => {
    setFormError(message);
    toast.error(message);
    if (fieldId) {
      setTimeout(() => {
        const el = document.getElementById(fieldId);
        if (el instanceof HTMLInputElement) {
          el.scrollIntoView?.({ block: 'center' });
          el.focus();
        }
      }, 0);
    }
  };

  const resetSubmitStateOnInput = () => {
    if (loading) {
      setLoading(false);
    }
    if (formError) {
      setFormError("");
    }
    setExistingEmail(null);
  };

  if (!authConfig.registrationEnabled) {
    return <Navigate to="/login" replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // The button already shows the in-flight / success state for these two.
    if (submitInFlightRef.current || success) return;
    setFormError("");
    setExistingEmail(null);

    if (!isEmailValid) {
      rejectSubmit(t('auth:errors.invalidEmail'), 'email');
      return;
    }

    if (password.length < 6) {
      rejectSubmit(t('auth:errors.shortPassword'), 'password');
      return;
    }

    // bcrypt only uses the first 72 bytes; the server rejects longer passwords.
    if (new TextEncoder().encode(password).length > MAX_PASSWORD_BYTES) {
      rejectSubmit(t('auth:errors.passwordTooLong'), 'password');
      return;
    }

    if (!acceptTerms) {
      rejectSubmit(t('auth:errors.mustAcceptTerms'));
      return;
    }

    submitInFlightRef.current = true;
    setLoading(true);

    try {
      const result = await register(trimmedEmail, password);
      if (result?.verification_sent === false) {
        const path = selectedPlan ? `/verify-email?${new URLSearchParams({ plan: selectedPlan }).toString()}` : '/verify-email';
        navigate(path, { state: { email: result.email || trimmedEmail, verificationSent: false } });
        return;
      }
      setSuccess(true);
      toast.success(t('auth:register.success'));
      setTimeout(() => {
        // The email travels in history state, not the URL, so it never shows
        // up in analytics page URLs. History state survives a reload.
        const verifyEmailPath = selectedPlan
          ? `/verify-email?${new URLSearchParams({ plan: selectedPlan }).toString()}`
          : '/verify-email';
        navigate(verifyEmailPath, { state: { email: trimmedEmail } });
      }, 2000);
    } catch (err: unknown) {
      const error = err as { message?: string };
      // Use generic error message to avoid exposing internal errors
      const message = error.message || t('auth:register.failed');
      setFormError(message);
      if (err instanceof ApiError && err.errorCode === 'ERR_AUTH_EMAIL_EXISTS') {
        setExistingEmail(trimmedEmail);
      }
      toast.error(message);
    } finally {
      submitInFlightRef.current = false;
      setLoading(false);
    }
  };

  const handleGoogleRegister = () => {
    setFormError("");
    googleLogin({ planIntent: selectedPlan });
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
              <div className="w-12 h-12 rounded-xl bg-[hsl(var(--primary))] flex items-center justify-center shadow-lg">
                <LogoMark className="w-7 h-7 text-[hsl(var(--primary-foreground))]" />
              </div>
            </div>
            <h1 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-2">{t('auth:register.title')}</h1>
            <p className="text-[hsl(var(--text-secondary))] text-sm">{t('auth:register.subtitle')}</p>
          </div>

        {/* Register Form */}
        <div className="bg-[hsl(var(--bg-secondary))] rounded-2xl p-6 sm:p-8 shadow-lg border border-[hsl(var(--border-color))]">
          {formError && (
            <div
              key={formError}
              id="register-form-error"
              role="alert"
              aria-live="assertive"
              className="bg-[hsl(var(--error)/0.1)] border border-[hsl(var(--error)/0.3)] rounded-xl p-4 mb-6 motion-error-shake"
            >
              <p className="text-[hsl(var(--error-text))] text-sm">{formError}</p>
              {existingEmail !== null && (
                <>
                  <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">{t('auth:register.emailExistsHint')}</p>
                  <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <Button
                      type="button"
                      size="touch"
                      onClick={() => navigate(loginLink, { state: { identifier: existingEmail } })}
                    >
                      {t('auth:register.emailExistsLogin')}
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      size="touch"
                      onClick={() => navigate(verifyEmailLink, { state: { email: existingEmail } })}
                    >
                      {t('auth:register.emailExistsVerify')}
                    </Button>
                  </div>
                </>
              )}
            </div>
          )}

          {hasPaidPlanIntent && (
            <div className="bg-[hsl(var(--accent-primary)/0.08)] border border-[hsl(var(--accent-primary)/0.22)] rounded-xl p-4 mb-6">
              <p className="text-[hsl(var(--text-primary))] text-sm font-medium">
                {t('auth:register.planIntentTitle', { plan: selectedPlanLabel })}
              </p>
              <p className="text-[hsl(var(--text-secondary))] text-xs mt-1">
                {t('auth:register.planIntentSubtitle')}
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
                    onClick={handleGoogleRegister}
                    className="w-full flex items-center justify-center gap-3 bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--bg-card))] text-[hsl(var(--text-primary))] font-medium py-3 rounded-xl border border-[hsl(var(--border-color))] transition-colors"
                  >
                    <GoogleIcon />
                    {t('auth:register.googleRegister')}
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

          {/* data-testid: register-form - Register form container for registration tests */}
          <form onSubmit={handleSubmit} data-testid="register-form" noValidate aria-busy={loading}>
            {/* Email */}
            <div className="mb-4">
              <label
                htmlFor="email"
                className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
              >
                {t('auth:register.emailLabel')}
              </label>
              <EmailSuggestionInput
                id="email"
                autoComplete="email"
                value={email}
                onValueChange={(value) => {
                  setEmail(value);
                  resetSubmitStateOnInput();
                }}
                onSuggestionChosen={() => document.getElementById("password")?.focus()}
                className="input min-h-11"
                placeholder={t('auth:register.emailPlaceholder')}
                required
                autoFocus
                disabled={loading || success}
                aria-invalid={trimmedEmail.length > 0 && !isEmailValid}
                aria-describedby={formError ? "register-form-error" : "register-email-helper"}
              />
              <p id="register-email-helper" className="mt-2 text-xs text-[hsl(var(--text-secondary))]">
                {t('auth:register.emailRule', '验证码会发到这个邮箱')}
              </p>
            </div>

            {/* Password */}
            <div className="mb-4">
              <label
                htmlFor="password"
                className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
              >
                {t('auth:register.passwordLabel')}
              </label>
              <div className="relative">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  resetSubmitStateOnInput();
                }}
                className="input min-h-11 pr-12"
                placeholder={t('auth:register.passwordHint')}
                required
                minLength={6}
                autoComplete="new-password"
                disabled={loading || success}
                aria-invalid={password.length > 0 && !isPasswordLengthValid}
                aria-describedby={formError ? "register-form-error" : "register-password-helper"}
              />
              <button
                type="button"
                onClick={() => setShowPassword((shown) => !shown)}
                className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-xl text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
                aria-label={showPassword ? t('auth:register.hidePassword', '隐藏密码') : t('auth:register.showPassword', '显示密码')}
                aria-pressed={showPassword}
                disabled={loading || success}
              >
                {showPassword ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
              </button>
              </div>
              <p
                id="register-password-helper"
                className={
                  password.length > 0 && !isPasswordLengthValid
                    ? "mt-2 text-xs text-[hsl(var(--text-secondary))]"
                    : "sr-only"
                }
              >
                {t('auth:register.passwordRule', '密码至少 6 个字符')}
              </p>
            </div>

            <div className="mb-6">
              <label className="flex min-h-11 cursor-pointer items-start gap-3 py-2 text-sm leading-6 text-[hsl(var(--text-secondary))]">
                <input
                  type="checkbox"
                  checked={acceptTerms}
                  onChange={(e) => {
                    setAcceptTerms(e.target.checked);
                    resetSubmitStateOnInput();
                  }}
                  className="mt-0.5 h-5 w-5 shrink-0 accent-[hsl(var(--accent-primary))]"
                  disabled={loading || success}
                />
                <span>
                  {t('auth:register.agreePrefix')}{" "}
                  <Link to="/terms-of-service" target="_blank" rel="noopener noreferrer" className="text-[hsl(var(--accent-primary))] underline underline-offset-2 hover:text-[hsl(var(--accent-light))]">
                    {t('auth:register.terms')}
                  </Link>{" "}
                  {t('auth:register.and')}{" "}
                  <Link to="/privacy-policy" target="_blank" rel="noopener noreferrer" className="text-[hsl(var(--accent-primary))] underline underline-offset-2 hover:text-[hsl(var(--accent-light))]">
                    {t('auth:register.privacy')}
                  </Link>
                </span>
              </label>
            </div>

            {/* Submit Button */}
            <Button
              type="submit"
              size="lg"
              disabled={submitDisabled}
              isLoading={loading}
              loadingText={t('auth:register.submitting')}
              className="w-full"
              aria-busy={loading}
            >
              {t('auth:register.submit')}
            </Button>
          </form>

          {/* Login Link */}
          <div className="mt-6 text-center">
            <p className="text-[hsl(var(--text-secondary))] text-sm">
              {t('auth:register.hasAccount')}{" "}
              <Link
                to={loginLink}
                className="text-[hsl(var(--accent-primary))] hover:text-[hsl(var(--accent-light))] font-medium transition-colors"
              >
                {t('auth:register.login')}
              </Link>
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="mt-8 text-center">
          <p className="text-[hsl(var(--text-secondary))] text-xs">
            {t('home:footer.copyright', { year: new Date().getFullYear() })}
          </p>
        </div>
        </div>
      </div>
    </div>
  );
}

export default Register;

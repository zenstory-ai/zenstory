import React, { useState, useEffect, useRef } from "react";
import { useNavigate, Link, Navigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LogoMark } from "../components/Logo";
import { useAuth } from "../contexts/AuthContext";
import { authConfig, hasOAuthProviders } from "../config/auth";
import { PublicHeader } from "../components/PublicHeader";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { InviteCodeInput } from "../components/referral/InviteCodeInput";
import { authApi } from "../lib/api";
import { translateError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { normalizePlanIntent, type PlanIntent } from "../lib/authFlow";

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

const AppleIcon = () => (
  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
    <path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.54 4.09l.01-.01zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.29 2.58-2.34 4.5-3.74 4.25z" />
  </svg>
);

export const Register: React.FC = () => {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [inviteCodePrefilled, setInviteCodePrefilled] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState<PlanIntent | null>(null);
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [formError, setFormError] = useState("");
  const [inviteCodeOptional, setInviteCodeOptional] = useState(authConfig.inviteCodeOptional);
  const { register, googleLogin, appleLogin } = useAuth();
  const navigate = useNavigate();
  const { t } = useTranslation(['auth', 'common', 'home']);
  const [searchParams] = useSearchParams();
  const policyRequestSeqRef = useRef(0);

  // Read invite code and plan intent from URL parameters
  useEffect(() => {
    setSelectedPlan(normalizePlanIntent(searchParams.get('plan')));

    const errorCode = searchParams.get('error_code');
    if (errorCode) {
      const message = translateError(errorCode);
      setFormError(message);
      toast.error(message);

      const inviteRelatedErrors = new Set([
        'ERR_AUTH_INVITE_CODE_REQUIRED',
        'ERR_REFERRAL_CODE_INVALID',
        'ERR_REFERRAL_CODE_EXPIRED',
        'ERR_REFERRAL_CODE_USED_UP',
      ]);

      // Focus invite code input only when the error is invite-related.
      if (inviteRelatedErrors.has(errorCode)) {
        setTimeout(() => {
          const el = document.getElementById('invite_code');
          if (el instanceof HTMLInputElement) {
            el.scrollIntoView({ block: 'center' });
            el.focus();
          }
        }, 0);
      }
    }

    const inviteFromUrl = searchParams.get('invite') || searchParams.get('code');
    if (!inviteFromUrl) {
      setInviteCode("");
      setInviteCodePrefilled(false);
      return;
    }

    // Format the code if needed (ensure XXXX-XXXX format)
    let formattedCode = inviteFromUrl.toUpperCase().replace(/[^A-Z0-9-]/g, '');
    if (formattedCode.length === 8 && !formattedCode.includes('-')) {
      formattedCode = formattedCode.slice(0, 4) + '-' + formattedCode.slice(4);
    }
    setInviteCode(formattedCode);
    setInviteCodePrefilled(true);
  }, [searchParams]);

  const hasPaidPlanIntent = selectedPlan !== null && selectedPlan !== 'free';
  const selectedPlanLabel = selectedPlan
    ? t(`auth:plans.${selectedPlan}`, selectedPlan.toUpperCase())
    : '';
  const loginLink = selectedPlan
    ? `/login?plan=${encodeURIComponent(selectedPlan)}`
    : "/login";
  const trimmedUsername = username.trim();
  const trimmedEmail = email.trim();
  const trimmedInviteCode = inviteCode.trim();
  const isUsernameValid = trimmedUsername.length >= 3;
  const isEmailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail);
  const isPasswordLengthValid = password.length >= 6;
  const isConfirmPasswordLengthValid = confirmPassword.length >= 6;
  const isPasswordMatched = password.length > 0 && password === confirmPassword;
  const isInviteCodeValid = inviteCodeOptional || trimmedInviteCode.length > 0;
  const canSubmit =
    !loading &&
    !success &&
    isUsernameValid &&
    isEmailValid &&
    isPasswordLengthValid &&
    isConfirmPasswordLengthValid &&
    isPasswordMatched &&
    isInviteCodeValid &&
    acceptTerms;

  useEffect(() => {
    if (!isUsernameValid || !isEmailValid) {
      setInviteCodeOptional(authConfig.inviteCodeOptional);
      return;
    }

    const requestSeq = ++policyRequestSeqRef.current;
    let cancelled = false;

    void authApi.getRegistrationPolicy({
      email: trimmedEmail,
      username: trimmedUsername,
    }).then((policy) => {
      if (!cancelled && requestSeq === policyRequestSeqRef.current) {
        setInviteCodeOptional(Boolean(policy.invite_code_optional));
      }
    }).catch(() => {
      if (!cancelled && requestSeq === policyRequestSeqRef.current) {
        setInviteCodeOptional(authConfig.inviteCodeOptional);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [isEmailValid, isUsernameValid, trimmedEmail, trimmedUsername]);

  const resetSubmitStateOnInput = () => {
    if (loading) {
      setLoading(false);
    }
    if (formError) {
      setFormError("");
    }
  };

  if (!authConfig.registrationEnabled) {
    return <Navigate to="/login" replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");

    if (password !== confirmPassword) {
      const message = t('auth:errors.passwordMismatch');
      setFormError(message);
      toast.error(message);
      return;
    }

    if (password.length < 6) {
      const message = t('auth:errors.shortPassword');
      setFormError(message);
      toast.error(message);
      return;
    }

    // bcrypt only uses the first 72 bytes; the server rejects longer passwords.
    if (new TextEncoder().encode(password).length > MAX_PASSWORD_BYTES) {
      const message = t('auth:errors.passwordTooLong');
      setFormError(message);
      toast.error(message);
      return;
    }

    if (trimmedUsername.length < 3) {
      const message = t('auth:errors.shortUsername');
      setFormError(message);
      toast.error(message);
      return;
    }

    let effectiveInviteCodeOptional = inviteCodeOptional;
    try {
      const policy = await authApi.getRegistrationPolicy({
        email: trimmedEmail,
        username: trimmedUsername,
      });
      effectiveInviteCodeOptional = Boolean(policy.invite_code_optional);
      setInviteCodeOptional(effectiveInviteCodeOptional);
    } catch {
      // Fall back to local config when policy API is unavailable.
    }

    if (!effectiveInviteCodeOptional && !trimmedInviteCode) {
      const message = t('auth:errors.inviteCodeRequired');
      setFormError(message);
      toast.error(message);
      return;
    }

    if (!acceptTerms) {
      const message = t('auth:errors.mustAcceptTerms');
      setFormError(message);
      toast.error(message);
      return;
    }

    setLoading(true);

    try {
      await register(trimmedUsername, trimmedEmail, password, trimmedInviteCode || undefined);
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
      toast.error(message);
    } finally {
      setLoading(false);
    }
  };

  const handleOAuthRegister = (provider: 'google' | 'apple') => {
    setFormError("");
    if (provider === 'google') {
      if (!inviteCodeOptional && !trimmedInviteCode) {
        const message = t('auth:errors.inviteCodeRequired');
        setFormError(message);
        toast.error(message);
        // Keep the user on the page so they can fill the code.
        setTimeout(() => {
          const el = document.getElementById('invite_code');
          if (el instanceof HTMLInputElement) {
            el.scrollIntoView({ block: 'center' });
            el.focus();
          }
        }, 0);
        return;
      }
      googleLogin({ inviteCode: trimmedInviteCode || undefined, planIntent: selectedPlan });
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
            <h1 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-2">{t('auth:register.title')}</h1>
            <p className="text-[hsl(var(--text-secondary))] text-sm">{t('auth:register.subtitle')}</p>
          </div>

        {/* Register Form */}
        <div className="bg-[hsl(var(--bg-secondary))] rounded-2xl p-6 sm:p-8 shadow-lg border border-[hsl(var(--border-color))]">
          {formError && (
            <div
              id="register-form-error"
              role="alert"
              aria-live="assertive"
              className="bg-[hsl(var(--error)/0.1)] border border-[hsl(var(--error)/0.3)] rounded-xl p-4 mb-6"
            >
              <p className="text-[hsl(var(--error))] text-sm">{formError}</p>
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
                    onClick={() => handleOAuthRegister('google')}
                    className="w-full flex items-center justify-center gap-3 bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--bg-card))] text-[hsl(var(--text-primary))] font-medium py-3 rounded-xl border border-[hsl(var(--border-color))] transition-colors"
                  >
                    <GoogleIcon />
                    {t('auth:register.googleRegister')}
                  </button>
                )}
                {authConfig.oauthProviders.apple.enabled && (
                  <button
                    type="button"
                    onClick={() => handleOAuthRegister('apple')}
                    className="w-full flex items-center justify-center gap-3 bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--bg-card))] text-[hsl(var(--text-primary))] font-medium py-3 rounded-xl border border-[hsl(var(--border-color))] transition-colors"
                  >
                    <AppleIcon />
                    {t('auth:register.appleRegister')}
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
            {/* Username */}
            <div className="mb-4">
              <label
                htmlFor="username"
                className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
              >
                {t('auth:register.usernameLabel')}
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => {
                  setUsername(e.target.value);
                  resetSubmitStateOnInput();
                }}
                className="input"
                placeholder={t('auth:register.usernameHint')}
                required
                autoFocus
                minLength={3}
                disabled={loading || success}
                aria-invalid={trimmedUsername.length > 0 && !isUsernameValid}
                aria-describedby={formError ? "register-form-error" : "register-username-helper"}
              />
              <p
                id="register-username-helper"
                className={
                  trimmedUsername.length > 0 && !isUsernameValid
                    ? "mt-2 text-xs text-[hsl(var(--text-secondary))]"
                    : "sr-only"
                }
              >
                {t('auth:register.usernameRule', '用户名至少 3 个字符')}
              </p>
            </div>

            {/* Email */}
            <div className="mb-4">
              <label
                htmlFor="email"
                className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
              >
                {t('auth:register.emailLabel')}
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  resetSubmitStateOnInput();
                }}
                className="input"
                placeholder={t('auth:register.emailPlaceholder')}
                required
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
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  resetSubmitStateOnInput();
                }}
                className="input"
                placeholder={t('auth:register.passwordHint')}
                required
                minLength={6}
                autoComplete="new-password"
                disabled={loading || success}
                aria-invalid={password.length > 0 && !isPasswordLengthValid}
                aria-describedby={formError ? "register-form-error" : "register-password-helper"}
              />
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

            {/* Confirm Password */}
            <div className="mb-4">
              <label
                htmlFor="confirmPassword"
                className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
              >
                {t('auth:register.confirmPasswordLabel')}
              </label>
              <input
                id="confirmPassword"
                type="password"
                value={confirmPassword}
                onChange={(e) => {
                  setConfirmPassword(e.target.value);
                  resetSubmitStateOnInput();
                }}
                className="input"
                placeholder={t('auth:register.confirmPasswordPlaceholder')}
                required
                minLength={6}
                autoComplete="new-password"
                disabled={loading || success}
                aria-invalid={confirmPassword.length > 0 && !isPasswordMatched}
                aria-describedby={
                  formError
                    ? "register-form-error"
                    : (confirmPassword.length > 0 && !isPasswordMatched)
                      ? "register-confirm-password-helper"
                      : undefined
                }
              />
              {confirmPassword.length > 0 && !isPasswordMatched ? (
                <p
                  id="register-confirm-password-helper"
                  className="mt-2 text-xs text-[hsl(var(--error))]"
                >
                  {t('auth:register.passwordMismatchHint', '两次输入的密码不一致')}
                </p>
              ) : null}
            </div>

            {/* Invite Code */}
            <div className="mb-6">
              <InviteCodeInput
                value={inviteCode}
                onChange={(value) => {
                  setInviteCode(value);
                  resetSubmitStateOnInput();
                }}
                disabled={loading || success}
                required={!inviteCodeOptional}
                prefilled={inviteCodePrefilled}
              />
            </div>

            <div className="mb-6">
              <label className="flex items-start gap-2 text-sm text-[hsl(var(--text-secondary))]">
                <input
                  type="checkbox"
                  checked={acceptTerms}
                  onChange={(e) => {
                    setAcceptTerms(e.target.checked);
                    resetSubmitStateOnInput();
                  }}
                  className="mt-0.5"
                  disabled={loading || success}
                />
                <span>
                  {t('auth:register.agreePrefix')}{" "}
                  <Link to="/terms-of-service" target="_blank" rel="noopener noreferrer" className="text-[hsl(var(--accent-primary))] hover:text-[hsl(var(--accent-light))]">
                    {t('auth:register.terms')}
                  </Link>{" "}
                  {t('auth:register.and')}{" "}
                  <Link to="/privacy-policy" target="_blank" rel="noopener noreferrer" className="text-[hsl(var(--accent-primary))] hover:text-[hsl(var(--accent-light))]">
                    {t('auth:register.privacy')}
                  </Link>
                </span>
              </label>
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={!canSubmit}
              className="btn-primary w-full py-3 text-base"
              aria-busy={loading}
            >
              {loading ? (
                <LoadingSpinner
                  size="sm"
                  variant="css"
                  color="white"
                  label={t('auth:register.submitting')}
                />
              ) : (
                t('auth:register.submit')
              )}
            </button>
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
          <p className="text-[hsl(var(--text-secondary))] text-xs opacity-60">
            {t('home:footer.copyright')}
          </p>
        </div>
        </div>
      </div>
    </div>
  );
}

export default Register;

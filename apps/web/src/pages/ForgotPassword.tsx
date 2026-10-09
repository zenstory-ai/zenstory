import React, { useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LogoMark } from "../components/Logo";
import { AlertCircle, Mail } from "../components/icons";
import { PublicHeader } from "../components/PublicHeader";
import { authConfig } from "../config/auth";
import { isPasswordResetUnavailable, passwordResetApi } from "../lib/passwordResetApi";
import { toast } from "../lib/toast";

const RESEND_COOLDOWN_SECONDS = 60;
const MAX_PASSWORD_BYTES = 72;
const SUPPORT_EMAIL = "support@zenstory.ai";

type Step = "email" | "code" | "unavailable";

export default function ForgotPassword() {
  const { t, i18n } = useTranslation(["auth"]);
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const inFlightRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  if (!authConfig.forgotPasswordEnabled) {
    return <Navigate to="/login" replace />;
  }

  const supportEmail = t("auth:forgotPassword.supportEmail", SUPPORT_EMAIL);
  const trimmedEmail = email.trim();
  const language = i18n?.language?.toLowerCase().startsWith("en") ? "en" : "zh";

  const run = async (operation: () => Promise<void>, fallbackMessage: string) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setLoading(true);
    setError("");
    try {
      await operation();
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      if (isPasswordResetUnavailable(err)) {
        setStep("unavailable");
        return;
      }
      const message = (err as { message?: string } | null)?.message;
      setError(message || fallbackMessage);
    } finally {
      inFlightRef.current = false;
      if (mountedRef.current) setLoading(false);
    }
  };

  const sendCode = () =>
    run(async () => {
      await passwordResetApi.requestCode(trimmedEmail, language);
      if (!mountedRef.current) return;
      setStep("code");
      setCode("");
      setCooldown(RESEND_COOLDOWN_SECONDS);
    }, t("auth:errors.resendFailed", "验证码发送失败，请稍后重试"));

  const handleEmailSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!trimmedEmail) {
      setError(t("auth:errors.invalidEmail", "邮箱格式不正确"));
      return;
    }
    void sendCode();
  };

  const handleResend = () => {
    if (cooldown > 0 || loading) return;
    void sendCode();
  };

  const handleResetSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!/^[0-9]{6}$/.test(code)) {
      setError(t("auth:errors.incompleteCode", "请输入完整的 6 位验证码"));
      return;
    }
    if (newPassword.length < 6) {
      setError(t("auth:errors.shortPassword", "密码至少 6 个字符"));
      return;
    }
    // bcrypt only uses the first 72 bytes; the server rejects longer passwords.
    if (new TextEncoder().encode(newPassword).length > MAX_PASSWORD_BYTES) {
      setError(t("auth:errors.passwordTooLong", "密码太长，请控制在 72 个英文字符或 24 个汉字以内"));
      return;
    }
    if (newPassword !== confirmPassword) {
      setError(t("auth:errors.passwordMismatch", "两次密码不一致"));
      return;
    }
    void run(async () => {
      await passwordResetApi.confirm(trimmedEmail, code, newPassword);
      if (!mountedRef.current) return;
      toast.success(t("auth:forgotPassword.success", "密码已重设，请用新密码登录"));
      navigate("/login", { replace: true });
    }, t("auth:forgotPassword.resetFailed", "密码没有重设成功，请稍后重试"));
  };

  const changeEmail = () => {
    setStep("email");
    setCode("");
    setNewPassword("");
    setConfirmPassword("");
    setError("");
  };

  const subtitle =
    step === "unavailable"
      ? t("auth:forgotPassword.unavailableSubtitle", "暂时没法在线重设密码，发邮件给我们就好")
      : step === "code"
        ? t("auth:forgotPassword.codeSentHint", "如果这个邮箱注册过 zenstory，验证码已经发出，10 分钟内有效。")
        : t("auth:forgotPassword.subtitle", "输入注册时用的邮箱，我们会发一个验证码给你");

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col">
      <PublicHeader variant="auth" maxWidth="max-w-6xl" />

      <div className="flex-1 flex items-center justify-center p-4 sm:p-6">
        <div className="w-full max-w-md">
          <div className="text-center mb-8 sm:mb-10">
            <div className="inline-flex items-center gap-2.5 mb-4">
              <div className="w-12 h-12 rounded-xl bg-[hsl(var(--accent-primary))] flex items-center justify-center shadow-lg">
                <LogoMark className="w-7 h-7 text-white" />
              </div>
            </div>
            <h1 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-2">
              {t("auth:forgotPassword.title", "找回密码")}
            </h1>
            <p className="text-[hsl(var(--text-secondary))] text-sm break-words" data-testid="forgot-password-subtitle">
              {subtitle}
            </p>
          </div>

          <div className="bg-[hsl(var(--bg-secondary))] rounded-2xl p-6 sm:p-8 shadow-lg border border-[hsl(var(--border-color))]">
            {error && (
              <div
                id="forgot-password-error"
                role="alert"
                aria-live="assertive"
                className="bg-[hsl(var(--error)/0.1)] border border-[hsl(var(--error)/0.3)] rounded-xl p-4 mb-6 flex items-start gap-3"
              >
                <AlertCircle className="w-5 h-5 text-[hsl(var(--error))] flex-shrink-0 mt-0.5" />
                <p className="text-[hsl(var(--error))] text-sm break-words">{error}</p>
              </div>
            )}

            {step === "email" && (
              <form onSubmit={handleEmailSubmit} aria-busy={loading} noValidate>
                <div className="mb-6">
                  <label
                    htmlFor="reset-email"
                    className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
                  >
                    {t("auth:forgotPassword.emailLabel", "注册邮箱")}
                  </label>
                  <input
                    id="reset-email"
                    type="email"
                    value={email}
                    onChange={(event) => {
                      setEmail(event.target.value);
                      if (error) setError("");
                    }}
                    className="input"
                    placeholder={t("auth:forgotPassword.emailPlaceholder", "输入邮箱")}
                    autoComplete="email"
                    autoFocus
                    required
                    disabled={loading}
                    aria-invalid={Boolean(error)}
                    aria-describedby={error ? "forgot-password-error" : undefined}
                  />
                </div>
                <button
                  type="submit"
                  className="btn-primary w-full py-3 text-base"
                  disabled={loading || !trimmedEmail}
                >
                  {loading
                    ? t("auth:forgotPassword.sending", "正在发送...")
                    : t("auth:forgotPassword.sendCode", "发送验证码")}
                </button>
              </form>
            )}

            {step === "code" && (
              <form onSubmit={handleResetSubmit} aria-busy={loading} noValidate>
                <p className="text-sm text-[hsl(var(--text-secondary))] mb-4 break-words">
                  {trimmedEmail}
                  <button
                    type="button"
                    onClick={changeEmail}
                    className="ml-2 text-[hsl(var(--accent-primary))] hover:text-[hsl(var(--accent-light))] font-medium transition-colors"
                    disabled={loading}
                  >
                    {t("auth:forgotPassword.changeEmail", "换一个邮箱")}
                  </button>
                </p>

                <div className="mb-4">
                  <label
                    htmlFor="reset-code"
                    className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
                  >
                    {t("auth:forgotPassword.codeLabel", "验证码")}
                  </label>
                  <input
                    id="reset-code"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    value={code}
                    onChange={(event) => {
                      setCode(event.target.value.replace(/[^0-9]/g, "").slice(0, 6));
                      if (error) setError("");
                    }}
                    className="input tracking-[0.3em]"
                    placeholder={t("auth:forgotPassword.codePlaceholder", "6 位数字")}
                    autoFocus
                    disabled={loading}
                  />
                </div>

                <div className="mb-4">
                  <label
                    htmlFor="reset-new-password"
                    className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
                  >
                    {t("auth:forgotPassword.newPasswordLabel", "新密码")}
                  </label>
                  <input
                    id="reset-new-password"
                    type="password"
                    value={newPassword}
                    onChange={(event) => {
                      setNewPassword(event.target.value);
                      if (error) setError("");
                    }}
                    className="input"
                    placeholder={t("auth:forgotPassword.newPasswordPlaceholder", "至少 6 个字符")}
                    autoComplete="new-password"
                    minLength={6}
                    disabled={loading}
                  />
                </div>

                <div className="mb-6">
                  <label
                    htmlFor="reset-confirm-password"
                    className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
                  >
                    {t("auth:forgotPassword.confirmPasswordLabel", "确认新密码")}
                  </label>
                  <input
                    id="reset-confirm-password"
                    type="password"
                    value={confirmPassword}
                    onChange={(event) => {
                      setConfirmPassword(event.target.value);
                      if (error) setError("");
                    }}
                    className="input"
                    placeholder={t("auth:forgotPassword.confirmPasswordPlaceholder", "再输入一次新密码")}
                    autoComplete="new-password"
                    disabled={loading}
                  />
                </div>

                <button type="submit" className="btn-primary w-full py-3 text-base" disabled={loading}>
                  {loading
                    ? t("auth:forgotPassword.submitting", "正在重设...")
                    : t("auth:forgotPassword.submit", "重设密码")}
                </button>

                <button
                  type="button"
                  onClick={handleResend}
                  disabled={cooldown > 0 || loading}
                  className="btn-ghost w-full py-3 mt-3 rounded-xl font-medium border border-[hsl(var(--border-color))] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {cooldown > 0
                    ? t("auth:forgotPassword.resendIn", { count: cooldown, defaultValue: "{{count}} 秒后可重新发送" })
                    : t("auth:forgotPassword.resend", "重新发送验证码")}
                </button>
              </form>
            )}

            {step === "unavailable" && (
              <>
                <div className="rounded-xl border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.08)] p-4 mb-6">
                  <div className="flex items-start gap-3">
                    <Mail className="w-5 h-5 text-[hsl(var(--accent-primary))] mt-0.5 shrink-0" />
                    <div>
                      <p className="text-sm text-[hsl(var(--text-primary))] font-medium">
                        {t("auth:forgotPassword.contactSupportTitle", "用注册邮箱发邮件给我们")}
                      </p>
                      <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">
                        {t("auth:forgotPassword.contactSupportHint", "我们核实账号后会尽快帮你重置密码。")}
                      </p>
                    </div>
                  </div>
                </div>

                <a
                  href={`mailto:${supportEmail}`}
                  className="btn-primary w-full py-3 text-base inline-flex items-center justify-center"
                >
                  {t("auth:forgotPassword.contactSupportAction", "发送邮件")}
                </a>
              </>
            )}

            <div className="mt-6 text-center">
              <Link
                to="/login"
                className="text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors"
              >
                {t("auth:login.backToLogin", "返回登录")}
              </Link>
            </div>
          </div>

          {step !== "unavailable" && (
            <p className="mt-6 text-center text-xs text-[hsl(var(--text-tertiary))]">
              {t("auth:forgotPassword.noEmailPrefix", "收不到邮件？发邮件给")}{" "}
              <a
                href={`mailto:${supportEmail}`}
                className="underline hover:text-[hsl(var(--text-secondary))] transition-colors"
              >
                {supportEmail}
              </a>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

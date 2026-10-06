import React, { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Mail, AlertCircle, CheckCircle } from "../components/icons";
import { useAuth } from "../contexts/AuthContext";
import { PublicHeader } from "../components/PublicHeader";
import { authConfig } from "../config/auth";
import { authApi } from "../lib/api";
import type { PlanIntent } from "../lib/authFlow";

interface VerifyEmailProps {
  email: string;
  planIntent?: PlanIntent | null;
}

export default function VerifyEmail({ email, planIntent }: VerifyEmailProps) {
  const navigate = useNavigate();
  const { t } = useTranslation(['auth']);
  const { verifyEmail, resendVerification } = useAuth();
  const normalizedEmail = email.trim();
  const hasEmail = normalizedEmail.length > 0;
  const shouldGoToBilling = planIntent !== null && planIntent !== undefined && planIntent !== "free";
  const loginLink = planIntent
    ? `/login?plan=${encodeURIComponent(planIntent)}`
    : "/login";
  const registerLink = planIntent
    ? `/register?plan=${encodeURIComponent(planIntent)}`
    : "/register";

  const [code, setCode] = useState<string[]>(["", "", "", "", "", ""]);
  const inputRefs = useRef<HTMLInputElement[]>([]);
  const automaticSubmitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pageLifetimeRef = useRef(0);
  const activeOperationRef = useRef<{ lifetime: number } | null>(null);
  const statusSequenceRef = useRef(0);

  const cancelAutomaticSubmit = useCallback(() => {
    if (automaticSubmitTimerRef.current !== null) {
      clearTimeout(automaticSubmitTimerRef.current);
      automaticSubmitTimerRef.current = null;
    }
  }, []);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  const [cooldown, setCooldown] = useState(60);
  const [codeTtl, setCodeTtl] = useState(300);

  const toNonNegativeInt = (value: unknown, fallback: number) => {
    if (typeof value === "number" && Number.isFinite(value)) {
      return Math.max(0, Math.floor(value));
    }

    if (typeof value === "string") {
      const parsed = Number.parseInt(value, 10);
      if (Number.isFinite(parsed)) {
        return Math.max(0, parsed);
      }
    }

    return fallback;
  };

  const syncVerificationStatus = useCallback(async (
    onCurrentFailure?: () => void,
  ): Promise<boolean> => {
    if (!hasEmail) return false;
    const lifetime = pageLifetimeRef.current;
    const sequence = ++statusSequenceRef.current;
    const isCurrent = () => (
      lifetime === pageLifetimeRef.current && sequence === statusSequenceRef.current
    );

    try {
      const status = await authApi.checkVerification(normalizedEmail);
      if (!isCurrent()) return false;
      setCooldown(toNonNegativeInt(status?.resend_cooldown_seconds, 0));
      setCodeTtl(toNonNegativeInt(status?.verification_code_ttl_seconds, 0));
      return true;
    } catch {
      if (isCurrent()) onCurrentFailure?.();
      return false;
    }
  }, [hasEmail, normalizedEmail]);

  useEffect(() => {
    if (cooldown > 0) {
      const timer = setTimeout(() => setCooldown(cooldown - 1), 1000);
      return () => clearTimeout(timer);
    }
  }, [cooldown]);

  useEffect(() => {
    if (codeTtl > 0 && !success) {
      const timer = setTimeout(() => setCodeTtl(codeTtl - 1), 1000);
      return () => clearTimeout(timer);
    }
  }, [codeTtl, success]);

  useEffect(() => {
    if (hasEmail && inputRefs.current[0]) {
      inputRefs.current[0]?.focus();
    }
  }, [hasEmail]);

  useEffect(() => {
    if (!hasEmail) {
      setError(t('auth:errors.missingVerificationEmail'));
      return;
    }
    setError((prev) => (prev === t('auth:errors.missingVerificationEmail') ? "" : prev));
  }, [hasEmail, t]);

  useEffect(() => {
    const lifetime = pageLifetimeRef.current;
    setLoading(false);
    if (hasEmail) void syncVerificationStatus();
    return () => {
      cancelAutomaticSubmit();
      pageLifetimeRef.current = lifetime + 1;
      activeOperationRef.current = null;
    };
  }, [hasEmail, syncVerificationStatus, cancelAutomaticSubmit]);

  const beginOperation = () => {
    if (activeOperationRef.current !== null || success) return null;
    const operation = { lifetime: pageLifetimeRef.current };
    activeOperationRef.current = operation;
    setLoading(true);
    return operation;
  };

  const ownsOperation = (operation: { lifetime: number }) => (
    operation.lifetime === pageLifetimeRef.current && activeOperationRef.current === operation
  );

  const scheduleAutomaticSubmit = (verificationCode: string) => {
    cancelAutomaticSubmit();
    const lifetime = pageLifetimeRef.current;
    automaticSubmitTimerRef.current = setTimeout(() => {
      automaticSubmitTimerRef.current = null;
      if (lifetime === pageLifetimeRef.current) void handleSubmit(verificationCode);
    }, 100);
  };

  const handleInputChange = (index: number, value: string) => {
    cancelAutomaticSubmit();
    const numValue = value.replace(/[^0-9]/g, "");

    const newCode = [...code];
    newCode[index] = numValue;
    setCode(newCode);

    if (numValue && index < 5 && inputRefs.current[index + 1]) {
      inputRefs.current[index + 1]?.focus();
    }

    if (error) setError("");

    if (index === 5 && numValue) {
      newCode[5] = numValue;
      scheduleAutomaticSubmit(newCode.join(""));
    }
  };

  const handleKeyDown = (index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace" && !code[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    e.preventDefault();
    cancelAutomaticSubmit();
    const pastedData = e.clipboardData.getData("text/plain");
    const numbers = pastedData.replace(/[^0-9]/g, "").slice(0, 6);

    if (numbers.length > 0) {
      const newCode = [...code];
      for (let i = 0; i < numbers.length; i++) {
        newCode[i] = numbers[i];
      }
      setCode(newCode);

      const nextIndex = Math.min(numbers.length, 5);
      if (inputRefs.current[nextIndex]) {
        inputRefs.current[nextIndex]?.focus();
      }

      if (numbers.length === 6) {
        scheduleAutomaticSubmit(numbers);
      }
    }
  };

  const handleSubmit = async (verificationCode: string) => {
    if (!hasEmail) {
      setError(t('auth:errors.missingVerificationEmail'));
      return;
    }

    if (verificationCode.length !== 6) {
      setError(t('auth:errors.incompleteCode'));
      return;
    }

    const operation = beginOperation();
    if (!operation) return;
    cancelAutomaticSubmit();
    setError("");

    try {
      await verifyEmail(normalizedEmail, verificationCode);
      if (!ownsOperation(operation)) return;
      setSuccess(true);

      setTimeout(() => {
        if (shouldGoToBilling && planIntent) {
          navigate(`/dashboard/billing?plan=${encodeURIComponent(planIntent)}`);
          return;
        }
        navigate("/dashboard");
      }, 1500);
    } catch (err: unknown) {
      if (!ownsOperation(operation)) return;
      if (err instanceof DOMException && err.name === 'AbortError') return;
      const error = err as { message?: string };
      setError(error.message || t('auth:errors.verificationFailed'));
      setCode(["", "", "", "", "", ""]);
      if (inputRefs.current[0]) {
        inputRefs.current[0]?.focus();
      }
    } finally {
      if (ownsOperation(operation)) {
        activeOperationRef.current = null;
        setLoading(false);
      }
    }
  };

  const handleResend = async () => {
    if (!hasEmail) {
      setError(t('auth:errors.missingVerificationEmail'));
      return;
    }

    if (cooldown > 0) return;

    const operation = beginOperation();
    if (!operation) return;
    cancelAutomaticSubmit();
    setError("");

    try {
      await resendVerification(normalizedEmail);
      if (!ownsOperation(operation)) return;
      await syncVerificationStatus(() => {
        setCooldown(60);
        setCodeTtl(300);
      });
      if (!ownsOperation(operation)) return;
      setSuccess(false);
      setCode(["", "", "", "", "", ""]);
      if (inputRefs.current[0]) {
        inputRefs.current[0]?.focus();
      }
    } catch (err: unknown) {
      if (!ownsOperation(operation)) return;
      const error = err as { message?: string };
      setError(error.message || t('auth:errors.resendFailed'));
    } finally {
      if (ownsOperation(operation)) {
        activeOperationRef.current = null;
        setLoading(false);
      }
    }
  };

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col">
      <PublicHeader variant="auth" />

      <div className="flex-1 flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          <div className="bg-[hsl(var(--bg-secondary))] rounded-2xl p-5 sm:p-8 shadow-lg border border-[hsl(var(--border-color))]">
            <div className="text-center mb-8">
              <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-[hsl(var(--accent-primary)/0.1)] mb-4">
                {success ? (
                  <CheckCircle className="w-8 h-8 text-[hsl(var(--success))]" />
                ) : (
                  <Mail className="w-8 h-8 text-[hsl(var(--accent-primary))]" />
                )}
              </div>
              <h1 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-2">
                {success ? t('auth:verifyEmail.successTitle') : t('auth:verifyEmail.title')}
              </h1>
              <p className="text-[hsl(var(--text-secondary))] text-sm break-words">
                {success
                  ? t('auth:verifyEmail.successSubtitle')
                  : hasEmail
                    ? t('auth:verifyEmail.subtitle', { email: normalizedEmail })
                    : t('auth:verifyEmail.missingEmailSubtitle')
                }
              </p>
            </div>

            {error && (
              <div className="bg-[hsl(var(--error)/0.1)] border border-[hsl(var(--error)/0.3)] rounded-xl p-4 mb-6 flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-[hsl(var(--error))] flex-shrink-0 mt-0.5" />
                <p className="text-[hsl(var(--error))] text-sm break-words">{error}</p>
              </div>
            )}

            {!success && hasEmail && (
              <>
                <div className="grid grid-cols-6 gap-1.5 sm:gap-2 mb-6">
                  {code.map((digit, index) => (
                    <input
                      key={index}
                      ref={(el) => { if (el) inputRefs.current[index] = el }}
                      type="text"
                      inputMode="numeric"
                      maxLength={1}
                      value={digit}
                      onChange={(e) => handleInputChange(index, e.target.value)}
                      onKeyDown={(e) => handleKeyDown(index, e)}
                      onPaste={handlePaste}
                      disabled={loading || success || !hasEmail}
                      className="w-full h-12 sm:h-14 text-center text-xl sm:text-2xl font-bold bg-[hsl(var(--bg-tertiary))] border-2 border-[hsl(var(--border-color))] rounded-xl focus:border-[hsl(var(--accent-primary))] focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    />
                  ))}
                </div>

                <div className="text-center mb-4">
                  <p className="text-[hsl(var(--text-secondary))] text-sm">
                    {codeTtl > 0 ? (
                      t('auth:verifyEmail.codeExpiring', { time: formatTime(codeTtl) })
                    ) : (
                      <span className="text-[hsl(var(--error))]">{t('auth:verifyEmail.codeExpired')}</span>
                    )}
                  </p>
                </div>

                <button
                  type="button"
                  onClick={handleResend}
                  disabled={cooldown > 0 || loading || !hasEmail}
                  className="btn-ghost w-full py-3 rounded-xl font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed border-2 border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.05)] text-[hsl(var(--text-primary))]"
                >
                  {cooldown > 0 ? (
                    t('auth:verifyEmail.resendButtonWithCount', { count: cooldown })
                  ) : (
                    t('auth:verifyEmail.resendButton')
                  )}
                </button>

                <div className="mt-6 text-center">
                  <button
                    type="button"
                    onClick={() => navigate(loginLink)}
                    className="text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors"
                  >
                    {t('auth:verifyEmail.backToLogin')}
                  </button>
                </div>
              </>
            )}

            {!success && !hasEmail && (
              <div className="space-y-3">
                {authConfig.registrationEnabled && (
                  <button
                    type="button"
                    onClick={() => navigate(registerLink)}
                    className="btn-primary w-full py-3"
                  >
                    {t('auth:verifyEmail.goToRegister')}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => navigate(loginLink)}
                  className="btn-ghost w-full py-3 rounded-xl font-medium border border-[hsl(var(--border-color))]"
                >
                  {t('auth:verifyEmail.backToLogin')}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

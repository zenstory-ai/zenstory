import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LogoMark } from "../components/Logo";
import { useAuth } from "../contexts/AuthContext";
import { PublicHeader } from "../components/PublicHeader";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { isValidRedirectUrl } from "../lib/ssoRedirect";
import { logger } from "../lib/logger";
import { captureException } from "../lib/analytics";
import { toUserErrorMessage } from "../lib/errorHandler";

export default function OAuthCallback() {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const { handleOAuthCallback, user } = useAuth();
  const navigate = useNavigate();
  const { t } = useTranslation(['auth', 'common']);

  const callbackProcessed = useRef(false);

  useEffect(() => {
    // Auth updates and StrictMode must not exchange the same callback twice.
    if (callbackProcessed.current) return;
    callbackProcessed.current = true;
    const processCallback = async () => {
      try {
        // Get OAuth callback params from both query and hash for compatibility.
        const queryParams = new URLSearchParams(window.location.search);
        const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ""));
        const accessToken = queryParams.get("access_token") || hashParams.get("access_token");
        const refreshToken = queryParams.get("refresh_token") || hashParams.get("refresh_token");
        const redirectUrl = queryParams.get("redirect") || hashParams.get("redirect");
        const providerError =
          queryParams.get("error") ||
          hashParams.get("error") ||
          queryParams.get("error_code") ||
          hashParams.get("error_code");

        // Capture first, then remove credentials/errors before any async work.
        // Preserve router history state and scrub failure paths as well as success.
        window.history.replaceState(window.history.state, document.title, window.location.pathname);

        if (providerError) {
          throw new Error(providerError);
        }

        if (!accessToken || !refreshToken) {
          const hasOAuthHandshakeParams =
            queryParams.has("code") ||
            queryParams.has("state") ||
            hashParams.has("code") ||
            hashParams.has("state");
          const hasCachedSession = Boolean(user);

          logger.warn("OAuth callback missing tokens", {
            hasOAuthHandshakeParams,
            hasCachedSession,
            pathname: window.location.pathname,
            hashPresent: Boolean(window.location.hash),
          });

          if (hasCachedSession) {
            navigate("/dashboard", { replace: true });
            return;
          }

          if (hasOAuthHandshakeParams) {
            setError(t("auth:errors.oauthFailed"));
            return;
          }

          navigate("/login", { replace: true });
          return;
        }

        // Handle OAuth callback
        await handleOAuthCallback(accessToken, refreshToken);

        // Check for redirect parameter from external apps
        if (redirectUrl) {
          if (!isValidRedirectUrl(redirectUrl)) {
            throw new Error("Invalid redirect URL");
          }
          // Redirect to external URL with token
          const redirectUrlWithToken = new URL(redirectUrl);
          redirectUrlWithToken.searchParams.set('token', accessToken);
          window.location.href = redirectUrlWithToken.toString();
          return;
        }

        // Redirect to dashboard
        navigate("/dashboard", { replace: true });
      } catch (err) {
        logger.error("OAuth callback error:", err);
        captureException(err, {
          feature_area: "auth",
          action: "oauth_callback",
        });
        const errorMessage = err instanceof Error
          ? toUserErrorMessage(err.message)
          : t('auth:errors.oauthFailed');
        setError(errorMessage);
      } finally {
        setLoading(false);
      }
    };

    void processCallback();
  }, [handleOAuthCallback, navigate, t, user]);

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col">
      {/* Header */}
      <PublicHeader variant="auth" />

      <div className="flex-1 flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          {/* Logo */}
          <div className="text-center mb-10">
            <div className="inline-flex items-center gap-2.5 mb-4">
              <div className="w-12 h-12 rounded-xl bg-[hsl(var(--accent-primary))] flex items-center justify-center shadow-lg">
                <LogoMark className="w-7 h-7 text-white" />
              </div>
            </div>
            <h1 className="text-2xl font-bold text-[hsl(var(--text-primary))] mb-2">
              {loading ? t('auth:login.loading') : t('auth:login.title')}
            </h1>
            <p className="text-[hsl(var(--text-secondary))] text-sm">
              {loading ? t('auth:login.verifying') : error ? t('auth:login.failed') : t('auth:login.redirecting')}
            </p>
          </div>

          {/* Status Card */}
          <div className="bg-[hsl(var(--bg-secondary))] rounded-2xl p-8 shadow-lg border border-[hsl(var(--border-color))]">
            {loading && (
              <LoadingSpinner
                size="xl"
                label={t('auth:login.oauthLoading')}
                vertical
              />
            )}

            {error && (
              <div className="flex flex-col items-center justify-center gap-4">
                <div className="w-16 h-16 rounded-full bg-[hsl(var(--error)/0.1)] flex items-center justify-center">
                  <svg
                    className="w-8 h-8 text-[hsl(var(--error))]"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </div>
                <div className="text-center">
                  <p className="text-[hsl(var(--error))] font-medium mb-2">{error}</p>
                  <p className="text-[hsl(var(--text-secondary))] text-sm">
                    {t('auth:errors.oauthErrorHint')}
                  </p>
                </div>
                <button
                  onClick={() => navigate("/login", { replace: true })}
                  className="btn-primary w-full py-3"
                >
                  {t('auth:login.backToLogin')}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

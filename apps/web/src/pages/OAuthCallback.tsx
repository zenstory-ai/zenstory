import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LogoMark } from "../components/Logo";
import { useAuth } from "../contexts/AuthContext";
import { PublicHeader } from "../components/PublicHeader";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { isValidRedirectUrl } from "../lib/ssoRedirect";
import { AUTH_CALLBACK_PATH, takeAuthCallbackParams } from "../lib/authCallbackParams";
import { logger } from "../lib/logger";
import { captureException } from "../lib/analytics";
import { toUserErrorMessage } from "../lib/errorHandler";
import { consumeOAuthPlanIntent } from "../lib/authFlow";

// Failures whose raw message (provider codes such as access_denied, internal
// checks) means nothing to the author; the page shows a generic message instead.
class OAuthGenericError extends Error {}

// Auth identity reconciliation can remount this page while its owner awaits /me.
let callbackInFlight = false;

export default function OAuthCallback() {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const { handleOAuthCallback, user } = useAuth();
  const navigate = useNavigate();
  const { t } = useTranslation(['auth', 'common']);

  const callbackProcessed = useRef(false);

  useEffect(() => {
    // Auth updates and StrictMode must not exchange the same callback twice.
    if (callbackProcessed.current || callbackInFlight || window.location.pathname !== AUTH_CALLBACK_PATH) return;
    callbackProcessed.current = true;
    const processCallback = async () => {
      let ownsAttempt = false;
      try {
        // main.tsx captures the callback params and scrubs the URL at boot,
        // before analytics starts. Fall back to the live URL (query, then
        // hash) when nothing was captured, e.g. a client-side navigation.
        const capturedParams = takeAuthCallbackParams();
        const queryParams = capturedParams ?? new URLSearchParams(window.location.search);
        const hashParams = capturedParams
          ? new URLSearchParams()
          : new URLSearchParams(window.location.hash.replace(/^#/, ""));
        const accessToken = queryParams.get("access_token") || hashParams.get("access_token");
        const refreshToken = queryParams.get("refresh_token") || hashParams.get("refresh_token");
        const redirectUrl = queryParams.get("redirect") || hashParams.get("redirect");
        const isNewUser = (queryParams.get("new_user") || hashParams.get("new_user")) === "1";
        const providerError =
          queryParams.get("error") ||
          hashParams.get("error") ||
          queryParams.get("error_code") ||
          hashParams.get("error_code");

        // Capture first, then remove credentials/errors before any async work.
        // Preserve router history state and scrub failure paths as well as success.
        window.history.replaceState(window.history.state, document.title, window.location.pathname);

        if (providerError) {
          throw providerError.startsWith("ERR_")
            ? new Error(providerError)
            : new OAuthGenericError(providerError);
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

        // Consume this attempt's intent before identity reconciliation remounts the page.
        callbackInFlight = true;
        ownsAttempt = true;
        const planIntent = consumeOAuthPlanIntent();
        await handleOAuthCallback(accessToken, refreshToken, { isNewUser });
        if (window.location.pathname !== AUTH_CALLBACK_PATH) {
          throw new DOMException('OAuth callback superseded', 'AbortError');
        }

        // Check for redirect parameter from external apps
        if (redirectUrl) {
          if (!isValidRedirectUrl(redirectUrl)) {
            throw new OAuthGenericError("Invalid redirect URL");
          }
          // Redirect to external URL with token
          const redirectUrlWithToken = new URL(redirectUrl);
          redirectUrlWithToken.searchParams.set('token', accessToken);
          window.location.href = redirectUrlWithToken.toString();
          return;
        }

        if (planIntent && planIntent !== 'free') {
          navigate(`/dashboard/billing?plan=${encodeURIComponent(planIntent)}`, { replace: true });
          return;
        }

        // Redirect to dashboard
        navigate("/dashboard", { replace: true });
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        logger.error("OAuth callback error:", err);
        captureException(err, {
          feature_area: "auth",
          action: "oauth_callback",
        });
        const errorMessage = err instanceof Error && !(err instanceof OAuthGenericError)
          ? toUserErrorMessage(err.message)
          : t('auth:errors.oauthFailed');
        setError(errorMessage);
      } finally {
        if (ownsAttempt) callbackInFlight = false;
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

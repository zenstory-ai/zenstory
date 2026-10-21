import React, { Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { PageLoader } from "./components/PageLoader";
import { AlertCircle } from "./components/icons";
import { Button } from "./components/ui/Button";
import { SEOHelmet } from "./components/Helmet";
import { SEOProvider } from "./providers/SEOProvider";
import { AuthIdentityQueryBoundary, AuthProvider, useAuth } from "./contexts/AuthContext";
import { ThemeProvider } from "./contexts/ThemeContext";
import { FileSearchProvider } from "./contexts/FileSearchContext";
import { CommonProviders } from "./providers/CommonProviders";
import { ProtectedProviders } from "./providers/ProtectedProviders";
import { ToastContainer } from "./components/Toast";
import { RouteChangeTracker } from "./components/RouteChangeTracker";
import { AppUpdatePrompt } from "./components/AppUpdatePrompt";
import { SiteBoundary } from "./components/SiteBoundary";
import { logger } from "./lib/logger";
import {
  LOGIN_ATTEMPT_KEY,
  RETURN_OWNER_KEY,
  lastSignedInUser,
  normalizePlanIntent,
  ownedReturnTarget,
  rememberSignedInUser,
  type LoginAttempt,
} from "./lib/authFlow";
import { clearAuthStorage, resolveOwnedAuthSession } from "./lib/apiClient";
import { onboardingPersonaApi, personaOnboardingQueryKey } from "./lib/onboardingPersonaApi";
import { lazyRoute } from "./lib/chunkRecovery";
import { inspirationsConfig } from "./config/inspirations";
import { useVisualViewport } from "./hooks/useVisualViewport";

// 懒加载路由组件 - 按需加载，减少首屏 bundle 大小
const ProjectEditor = lazyRoute(() => import("./pages/ProjectEditor"), "ProjectEditor");
const HomePage = lazyRoute(() => import("./pages/HomePage"), "HomePage");
const Dashboard = lazyRoute(() => import("./pages/Dashboard"), "Dashboard");
const DashboardHome = lazyRoute(() => import("./pages/DashboardHome"), "DashboardHome");
const Login = lazyRoute(() => import("./pages/Login"), "Login");
const Register = lazyRoute(() => import("./pages/Register"), "Register");
const VerifyEmail = lazyRoute(() => import("./pages/VerifyEmail"), "VerifyEmail");
const ForgotPassword = lazyRoute(() => import("./pages/ForgotPassword"), "ForgotPassword");
const OAuthCallback = lazyRoute(() => import("./pages/OAuthCallback"), "OAuthCallback");
const PrivacyPolicy = lazyRoute(() => import("./pages/PrivacyPolicy"), "PrivacyPolicy");
const TermsOfService = lazyRoute(() => import("./pages/TermsOfService"), "TermsOfService");
const DocsPage = lazyRoute(() => import("./pages/DocsPage"), "DocsPage");
const PricingPage = lazyRoute(() => import("./pages/PricingPage"), "PricingPage");
const SkillsPage = lazyRoute(() => import("./pages/SkillsPage"), "SkillsPage");
const MaterialsPage = lazyRoute(() => import("./pages/MaterialsPage"), "MaterialsPage");
const MaterialDetailPage = lazyRoute(() => import("./pages/MaterialDetailPage"), "MaterialDetailPage");
const DashboardProjects = lazyRoute(() => import("./pages/DashboardProjects"), "DashboardProjects");
const ProjectDashboardPage = lazyRoute(() => import("./pages/ProjectDashboardPage"), "ProjectDashboardPage");
const BillingPage = lazyRoute(() => import("./pages/BillingPage"), "BillingPage");
const PaymentReturnPage = lazyRoute(() => import("./pages/PaymentReturnPage"), "PaymentReturnPage");
const OnboardingPersonaPage = lazyRoute(() => import("./pages/OnboardingPersonaPage"), "OnboardingPersonaPage");

// Inspiration pages
const InspirationsPage = lazyRoute(() => import("./pages/InspirationsPage"), "InspirationsPage");
const InspirationDetailPage = lazyRoute(() => import("./pages/InspirationDetailPage"), "InspirationDetailPage");

// Admin pages
const AdminLayout = lazyRoute(() => import("./components/admin/AdminLayout"), "AdminLayout");
const AdminDashboard = lazyRoute(() => import("./pages/admin/AdminDashboard"), "AdminDashboard");
const UserManagement = lazyRoute(() => import("./pages/admin/UserManagement"), "UserManagement");
const AdminUserDetail = lazyRoute(() => import("./pages/admin/AdminUserDetail"), "AdminUserDetail");
const PromptManagement = lazyRoute(() => import("./pages/admin/PromptManagement"), "PromptManagement");
const PromptEditor = lazyRoute(() => import("./pages/admin/PromptEditor"), "PromptEditor");
const SkillReviewPage = lazyRoute(() => import("./pages/admin/SkillReviewPage"), "SkillReviewPage");
const CodeManagement = lazyRoute(() => import("./pages/admin/CodeManagement"), "CodeManagement");
const SubscriptionManagement = lazyRoute(() => import("./pages/admin/SubscriptionManagement"), "SubscriptionManagement");
const PaymentOrderManagement = lazyRoute(() => import("./pages/admin/PaymentOrderManagement"), "PaymentOrderManagement");
const SubscriptionPlanManagement = lazyRoute(() => import("./pages/admin/SubscriptionPlanManagement"), "SubscriptionPlanManagement");
const AuditLogPage = lazyRoute(() => import("./pages/admin/AuditLogPage"), "AuditLogPage");
const InspirationManagement = lazyRoute(() => import("./pages/admin/InspirationManagement"), "InspirationManagement");
const FeedbackManagement = lazyRoute(() => import("./pages/admin/FeedbackManagement"), "FeedbackManagement");
const AnnouncementManagement = lazyRoute(() => import("./pages/admin/AnnouncementManagement"), "AnnouncementManagement");
// Commercial pages
const PointsManagement = lazyRoute(() => import("./pages/admin/PointsManagement"), "PointsManagement");
const CheckInStatsPage = lazyRoute(() => import("./pages/admin/CheckInStatsPage"), "CheckInStatsPage");
const ReferralManagement = lazyRoute(() => import("./pages/admin/ReferralManagement"), "ReferralManagement");
const QuotaManagement = lazyRoute(() => import("./pages/admin/QuotaManagement"), "QuotaManagement");
const UsageCostPage = lazyRoute(() => import("./pages/admin/UsageCostPage"), "UsageCostPage");
const AdminRoute = lazyRoute(() => import("./components/AdminRoute"), "AdminRoute");

// Protected route wrapper - redirects to login if not authenticated
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  const { t } = useTranslation("common");
  const isOnboardingRoute = location.pathname.startsWith("/onboarding/persona");
  const isAdminRoute = location.pathname.startsWith("/admin");
  const shouldCheckOnboarding = Boolean(user && !loading && !isOnboardingRoute && !isAdminRoute);
  const onboardingState = useQuery({
    queryKey: personaOnboardingQueryKey(user?.id ?? "anonymous"),
    queryFn: onboardingPersonaApi.getState,
    enabled: shouldCheckOnboarding,
  });
  const signedInId = user?.id;
  React.useEffect(() => {
    if (signedInId) rememberSignedInUser(signedInId);
  }, [signedInId]);

  if (loading) {
    return <PageLoader />;
  }

  if (!user) {
    // 回跳目标记下归属账号：登出 A 后换 B 登录时不再回到 A 的页面。
    const owner = lastSignedInUser();
    return <Navigate to="/login" state={{ from: location, ...(owner ? { [RETURN_OWNER_KEY]: owner } : {}) }} replace />;
  }

  if (shouldCheckOnboarding && onboardingState.isPending) {
    return <PageLoader />;
  }

  if (shouldCheckOnboarding && onboardingState.isError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[hsl(var(--bg-primary))] p-4">
        <div className="w-full max-w-sm rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-6 text-center" role="alert">
          <AlertCircle aria-hidden="true" className="mx-auto mb-3 h-6 w-6 text-[hsl(var(--error-text))]" />
          <p className="mb-5 text-sm leading-6 text-[hsl(var(--text-secondary))]">{t("onboarding:errors.loadFailed", "加载失败，请检查网络后重试")}</p>
          <Button size="touch" className="w-full" onClick={() => void onboardingState.refetch()}>
            {t("retry", "重试")}
          </Button>
        </div>
      </div>
    );
  }

  if (shouldCheckOnboarding && onboardingState.data?.required) {
    const search = location.search || (typeof window !== "undefined" ? window.location.search : "");
    const hash = location.hash || (typeof window !== "undefined" ? window.location.hash : "");

    return (
      <Navigate
        to="/onboarding/persona"
        state={{
          from: {
            pathname: location.pathname,
            search,
            hash,
          },
        }}
        replace
      />
    );
  }

  return <>{children}</>;
}

// Public route wrapper - redirects to dashboard if already authenticated
function PublicRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  const [ssoState, setSsoState] = React.useState<'idle' | 'validating' | 'redirecting' | 'failed'>('idle');
  const [ssoFailureTarget, setSsoFailureTarget] = React.useState<'dashboard' | 'login'>('dashboard');

  const userId = user?.id;
  React.useEffect(() => {
    let active = true;
    const cleanup = () => { active = false; };
    // A pending lazy destination can retain this Suspense subtree without
    // running its passive cleanup. The browser URL already owns navigation.
    const isCurrentEntry = () => active
      && window.location.pathname === location.pathname
      && window.location.search === location.search;
    if (loading) return cleanup;

    const redirectUrl = new URLSearchParams(location.search).get('redirect');
    if (!userId || !redirectUrl) {
      setSsoState('idle');
      return cleanup;
    }

    const entryAccess = localStorage.getItem('access_token');
    const entryRefresh = localStorage.getItem('refresh_token');
    setSsoState('validating');
    setSsoFailureTarget('dashboard');
    void (async () => {
      let redirected = false;
      try {
        const { handleSsoRedirect } = await import('./lib/ssoRedirect');
        if (!isCurrentEntry()) return;
        const result = await handleSsoRedirect(redirectUrl);
        if (!isCurrentEntry()) return;
        const owned = resolveOwnedAuthSession(entryAccess, entryRefresh);
        const empty = !localStorage.getItem('access_token') && !localStorage.getItem('refresh_token');
        if (!owned && !empty) return;
        if (result.success && result.redirectUrl) {
          if (!owned?.accessToken) return;
          setSsoState('redirecting');
          redirected = true;
          logger.log('[PublicRoute] SSO validated, redirecting...');
          window.location.href = result.redirectUrl;
          return;
        }

        logger.warn('[PublicRoute] SSO validation failed:', result.error);
        if (result.clearAuth || (empty && result.reason === 'session_expired')) {
          if (owned && !empty) clearAuthStorage('sso_redirect_failed');
          if (!isCurrentEntry()) return;
          setSsoFailureTarget('login');
        } else {
          setSsoFailureTarget('dashboard');
        }
      } catch (error) {
        if (!isCurrentEntry() || (error instanceof DOMException && error.name === 'AbortError')) return;
        logger.warn('[PublicRoute] SSO validation error:', error);
        setSsoFailureTarget('dashboard');
      } finally {
        if (isCurrentEntry() && !redirected) setSsoState('failed');
      }
    })();
    return cleanup;
  }, [userId, loading, location.key, location.pathname, location.search]);

  if (loading || ssoState === 'validating' || ssoState === 'redirecting') {
    return <PageLoader />;
  }

  if (ssoState === 'failed') {
    return ssoFailureTarget === 'dashboard'
      ? <Navigate to="/dashboard" replace />
      : <>{children}</>;
  }

  if (user) {
    const searchParams = new URLSearchParams(location.search);
    const redirectUrl = searchParams.get('redirect');

    // If there's a redirect URL and we're still processing, show loader
    if (redirectUrl && ssoState === 'idle') {
      return <PageLoader />;
    }

    // No redirect URL - go to dashboard
    if (!redirectUrl) {
      const state = location.state as Record<string, unknown> | null;
      // Router state can still describe the pre-submit entry during identity reconciliation.
      const attempt = window.history.state?.usr?.[LOGIN_ATTEMPT_KEY] as LoginAttempt | undefined;
      const continuationState = attempt?.kind === 'login' && typeof attempt.id === 'string'
        ? { [LOGIN_ATTEMPT_KEY]: attempt } : {};
      const from = ownedReturnTarget(state, user.id);
      if (from) {
        return <Navigate to={`${from.pathname}${from.search ?? ''}${from.hash ?? ''}`}
          state={{ ...from.state, ...continuationState }} replace />;
      }
      const planIntent = normalizePlanIntent(searchParams.get('plan'));
      return planIntent && planIntent !== 'free'
        ? <Navigate to={`/dashboard/billing?plan=${encodeURIComponent(planIntent)}`} state={continuationState} replace />
        : <Navigate to="/dashboard" state={continuationState} replace />;
    }
  }

  return <>{children}</>;
}

// Verify email wrapper - the email comes from navigation state (set by
// Register; kept across reloads by the browser),
// then a legacy `?email=` link, then the signed-in user.
function VerifyEmailWrapper() {
  const { user, loading } = useAuth();
  const location = useLocation();
  const searchParams = new URLSearchParams(location.search);
  const stateEmail = (location.state as { email?: unknown } | null)?.email;
  const emailFromState = typeof stateEmail === 'string' ? stateEmail.trim() : '';
  const emailFromQuery = searchParams.get('email')?.trim() || '';
  const userEmail = user?.email?.trim() || '';
  const email = emailFromState || emailFromQuery || userEmail;
  const planIntent = normalizePlanIntent(searchParams.get('plan'));

  // If already authenticated and email verified, redirect to dashboard
  if (!loading && user && user.email_verified) {
    return <Navigate to="/dashboard" replace />;
  }

  return <VerifyEmail email={email} planIntent={planIntent} />;
}


function App() {
  useVisualViewport();
  return (
    <HelmetProvider>
      <BrowserRouter>
        <SiteBoundary>
          <ThemeProvider>
            <FileSearchProvider>
              <AuthProvider>
                <AuthIdentityQueryBoundary>
                <SEOProvider>
                  <SEOHelmet />
                  <RouteChangeTracker />
                  <Suspense fallback={<PageLoader />}>
                    <Routes>
                    {/* Public routes */}
                    <Route
                      path="/"
                      element={
                        <CommonProviders>
                          <PublicRoute>
                            <HomePage />
                          </PublicRoute>
                        </CommonProviders>
                      }
                    />
                    <Route path="/privacy-policy" element={<PrivacyPolicy />} />
                    <Route path="/terms-of-service" element={<TermsOfService />} />
                    {/* Docs routes - public access */}
                    <Route path="/docs" element={<DocsPage />} />
                    <Route path="/docs/*" element={<DocsPage />} />
                    <Route path="/pricing" element={<PricingPage />} />
                    <Route path="/auth/callback" element={<OAuthCallback />} />
                    <Route
                      path="/login"
                      element={
                        <CommonProviders>
                          <PublicRoute>
                            <Login />
                          </PublicRoute>
                        </CommonProviders>
                      }
                    />
                    <Route
                      path="/register"
                      element={
                        <CommonProviders>
                          <PublicRoute>
                            <Register />
                          </PublicRoute>
                        </CommonProviders>
                      }
                    />
                    <Route
                      path="/forgot-password"
                      element={
                        <CommonProviders>
                          <PublicRoute>
                            <ForgotPassword />
                          </PublicRoute>
                        </CommonProviders>
                      }
                    />
                    <Route path="/verify-email" element={<VerifyEmailWrapper />} />

                    {/* Protected routes */}
                    <Route
                      path="/onboarding/persona"
                      element={
                        <ProtectedRoute>
                          <OnboardingPersonaPage />
                        </ProtectedRoute>
                      }
                    />
                    <Route
                      path="/dashboard"
                      element={
                        <ProtectedRoute>
                          <ProtectedProviders>
                            <Dashboard />
                          </ProtectedProviders>
                        </ProtectedRoute>
                      }
                    >
                      <Route index element={<DashboardHome />} />
                      <Route path="projects" element={<DashboardProjects />} />
                      <Route path="materials" element={<MaterialsPage />} />
                      <Route path="skills" element={<SkillsPage />} />
                      <Route path="billing" element={<BillingPage />} />
                      <Route path="billing/payment-return" element={<PaymentReturnPage />} />
                      <Route path="inspirations" element={inspirationsConfig.enabled ? <InspirationsPage /> : <Navigate to="/dashboard" replace />} />
                      <Route path="inspirations/:inspirationId" element={inspirationsConfig.enabled ? <InspirationDetailPage /> : <Navigate to="/dashboard" replace />} />
                    </Route>
                    <Route
                      path="/project/:projectId"
                      element={
                        <ProtectedRoute>
                          <ProtectedProviders>
                            <ProjectEditor />
                          </ProtectedProviders>
                        </ProtectedRoute>
                      }
                    />
                    <Route
                      path="/project/:projectId/dashboard"
                      element={
                        <ProtectedRoute>
                          <ProtectedProviders>
                            <ProjectDashboardPage />
                          </ProtectedProviders>
                        </ProtectedRoute>
                      }
                    />
                    <Route
                      path="/materials/:novelId"
                      element={
                        <ProtectedRoute>
                          <ProtectedProviders>
                            <MaterialDetailPage />
                          </ProtectedProviders>
                        </ProtectedRoute>
                      }
                    />

                    {/* Admin routes */}
                    <Route
                      path="/admin"
                      element={
                        <AdminRoute>
                          <AdminLayout />
                        </AdminRoute>
                      }
                    >
                      <Route index element={<AdminDashboard />} />
                      <Route path="users" element={<UserManagement />} />
                      <Route path="usage" element={<UsageCostPage />} />
                      <Route path="users/:userId" element={<AdminUserDetail />} />
                      <Route path="prompts" element={<PromptManagement />} />
                      <Route path="prompts/:projectType" element={<PromptEditor />} />
                      <Route path="skills" element={<SkillReviewPage />} />
                      <Route path="codes" element={<CodeManagement />} />
                      <Route path="subscriptions" element={<SubscriptionManagement />} />
                      <Route path="payment-orders" element={<PaymentOrderManagement />} />
                      <Route path="plans" element={<SubscriptionPlanManagement />} />
                      <Route path="audit-logs" element={<AuditLogPage />} />
                      <Route path="inspirations" element={inspirationsConfig.enabled ? <InspirationManagement /> : <Navigate to="/admin" replace />} />
                      <Route path="feedback" element={<FeedbackManagement />} />
                      <Route path="announcements" element={<AnnouncementManagement />} />
                      {/* Commercial routes */}
                      <Route path="points" element={<PointsManagement />} />
                      <Route path="check-in" element={<CheckInStatsPage />} />
                      <Route path="referrals" element={<ReferralManagement />} />
                      <Route path="quota" element={<QuotaManagement />} />
                    </Route>

                    {/* Fallback - redirect to home */}
                    <Route path="*" element={<Navigate to="/" replace />} />
                    </Routes>
                  </Suspense>
                </SEOProvider>
                <ToastContainer />
                <AppUpdatePrompt />
                </AuthIdentityQueryBoundary>
              </AuthProvider>
            </FileSearchProvider>
          </ThemeProvider>
        </SiteBoundary>
      </BrowserRouter>
    </HelmetProvider>
  );
}

export default App;

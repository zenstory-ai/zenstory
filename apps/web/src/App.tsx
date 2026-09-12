import React, { Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate, useParams, useNavigate, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { useTranslation } from "react-i18next";
import { Layout } from "./components/Layout";
import { Sidebar } from "./components/sidebar/Sidebar";
import { Editor } from "./components/Editor";
import { ChatPanel } from "./components/ChatPanel";
import { PageLoader } from "./components/PageLoader";
import { SEOHelmet } from "./components/Helmet";
import { SEOProvider } from "./providers/SEOProvider";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { ThemeProvider } from "./contexts/ThemeContext";
import { FileSearchProvider } from "./contexts/FileSearchContext";
import { useProject } from "./contexts/ProjectContext";
import { CommonProviders } from "./providers/CommonProviders";
import { ProtectedProviders } from "./providers/ProtectedProviders";
import { ToastContainer } from "./components/Toast";
import { RouteChangeTracker } from "./components/RouteChangeTracker";
import { SiteBoundary } from "./components/SiteBoundary";
import { logger } from "./lib/logger";
import { fileApi } from "./lib/api";
import { normalizePlanIntent } from "./lib/authFlow";
import { shouldRequirePersonaOnboarding } from "./lib/onboardingPersona";
import type { TreeNodeType } from "./types";
import { lazyRoute } from "./lib/chunkRecovery";

// 懒加载路由组件 - 按需加载，减少首屏 bundle 大小
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
const OnboardingPersonaPage = lazyRoute(() => import("./pages/OnboardingPersonaPage"), "OnboardingPersonaPage");

// Inspiration pages
const InspirationsPage = lazyRoute(() => import("./pages/InspirationsPage"), "InspirationsPage");
const InspirationDetailPage = lazyRoute(() => import("./pages/InspirationDetailPage"), "InspirationDetailPage");

// Admin pages
const AdminLayout = lazyRoute(() => import("./components/admin/AdminLayout"), "AdminLayout");
const AdminDashboard = lazyRoute(() => import("./pages/admin/AdminDashboard"), "AdminDashboard");
const UserManagement = lazyRoute(() => import("./pages/admin/UserManagement"), "UserManagement");
const PromptManagement = lazyRoute(() => import("./pages/admin/PromptManagement"), "PromptManagement");
const PromptEditor = lazyRoute(() => import("./pages/admin/PromptEditor"), "PromptEditor");
const SkillReviewPage = lazyRoute(() => import("./pages/admin/SkillReviewPage"), "SkillReviewPage");
const CodeManagement = lazyRoute(() => import("./pages/admin/CodeManagement"), "CodeManagement");
const SubscriptionManagement = lazyRoute(() => import("./pages/admin/SubscriptionManagement"), "SubscriptionManagement");
const SubscriptionPlanManagement = lazyRoute(() => import("./pages/admin/SubscriptionPlanManagement"), "SubscriptionPlanManagement");
const AuditLogPage = lazyRoute(() => import("./pages/admin/AuditLogPage"), "AuditLogPage");
const InspirationManagement = lazyRoute(() => import("./pages/admin/InspirationManagement"), "InspirationManagement");
const FeedbackManagement = lazyRoute(() => import("./pages/admin/FeedbackManagement"), "FeedbackManagement");
// Commercial pages
const PointsManagement = lazyRoute(() => import("./pages/admin/PointsManagement"), "PointsManagement");
const CheckInStatsPage = lazyRoute(() => import("./pages/admin/CheckInStatsPage"), "CheckInStatsPage");
const ReferralManagement = lazyRoute(() => import("./pages/admin/ReferralManagement"), "ReferralManagement");
const QuotaManagement = lazyRoute(() => import("./pages/admin/QuotaManagement"), "QuotaManagement");
const AdminRoute = lazyRoute(() => import("./components/AdminRoute"), "AdminRoute");

// Protected route wrapper - redirects to login if not authenticated
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <PageLoader />;
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  const isOnboardingRoute = location.pathname.startsWith("/onboarding/persona");
  const isAdminRoute = location.pathname.startsWith("/admin");
  const shouldRequireOnboarding = !isOnboardingRoute && !isAdminRoute;

  if (shouldRequireOnboarding && shouldRequirePersonaOnboarding(user)) {
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
  const [ssoState, setSsoState] = React.useState<'idle' | 'validating' | 'redirecting' | 'failed'>('idle');

  React.useEffect(() => {
    // Only run SSO validation once when user is loaded
    if (loading || ssoState !== 'idle' || !user) return;

    const searchParams = new URLSearchParams(window.location.search);
    const redirectUrl = searchParams.get('redirect');

    if (redirectUrl) {
      setSsoState('validating');

      // Import and use handleSsoRedirect with validation
      import('./lib/ssoRedirect').then(async ({ handleSsoRedirect }) => {
        const result = await handleSsoRedirect(redirectUrl);

        if (result.success && result.redirectUrl) {
          setSsoState('redirecting');
          logger.log('[PublicRoute] SSO validated, redirecting...');
          window.location.href = result.redirectUrl;
        } else {
          // SSO validation failed - clear tokens and reload to show login
          logger.warn('[PublicRoute] SSO validation failed:', result.error);
          setSsoState('failed');
          // Clear invalid tokens
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          localStorage.removeItem('user');
          // Reload to trigger re-authentication
          window.location.reload();
        }
      });
    }
  }, [user, loading, ssoState]);

  if (loading || ssoState === 'validating' || ssoState === 'redirecting') {
    return <PageLoader />;
  }

  if (user) {
    const searchParams = new URLSearchParams(window.location.search);
    const redirectUrl = searchParams.get('redirect');

    // If there's a redirect URL and we're still processing, show loader
    if (redirectUrl && ssoState === 'idle') {
      return <PageLoader />;
    }

    // If SSO failed, we're about to reload - show loader
    if (ssoState === 'failed') {
      return <PageLoader />;
    }

    // No redirect URL - go to dashboard
    if (!redirectUrl) {
      return <Navigate to="/dashboard" replace />;
    }
  }

  return <>{children}</>;
}

// Verify email wrapper - extracts email from URL params
function VerifyEmailWrapper() {
  const { user, loading } = useAuth();
  const searchParams = new URLSearchParams(window.location.search);
  const emailFromQuery = searchParams.get('email')?.trim() || '';
  const userEmail = user?.email?.trim() || '';
  const email = emailFromQuery || userEmail;
  const planIntent = normalizePlanIntent(searchParams.get('plan'));

  // If already authenticated and email verified, redirect to dashboard
  if (!loading && user && user.email_verified) {
    return <Navigate to="/dashboard" replace />;
  }

  return <VerifyEmail email={email} planIntent={planIntent} />;
}

// Project editor wrapper - loads project from URL param
function ProjectEditor() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { setCurrentProjectId, currentProject, loading, error, refreshProjects, projects, setSelectedItem } = useProject();
  const { t } = useTranslation();

  const [isEnsuring, setIsEnsuring] = React.useState(false);
  const [notFound, setNotFound] = React.useState(false);
  const attemptedRefreshRef = React.useRef<string | null>(null);
  const selectedFromQueryRef = React.useRef<string | null>(null);

  // Reset per projectId
  React.useEffect(() => {
    attemptedRefreshRef.current = null;
    selectedFromQueryRef.current = null;
    setNotFound(false);
  }, [projectId]);

  // Select target file from query parameter: /project/:projectId?file=:fileId
  React.useEffect(() => {
    // Wait until the route's project is active. On a direct link the provider
    // can briefly expose the previously selected project; selecting the file
    // during that window is then cleared by setCurrentProjectId().
    if (!currentProject || !projectId || currentProject.id !== projectId) return;

    const searchParams = new URLSearchParams(location.search);
    const fileId = searchParams.get("file");

    if (!fileId) {
      selectedFromQueryRef.current = null;
      return;
    }

    // Prevent repeated network requests for the same query value.
    if (selectedFromQueryRef.current === fileId) return;

    let cancelled = false;

    const selectFileFromQuery = async () => {
      try {
        const file = await fileApi.get(fileId);
        if (cancelled) return;

        // Guard against cross-project query values.
        if (file.project_id !== projectId) {
          logger.warn("[ProjectEditor] Ignored file query param from different project", {
            projectId,
            fileProjectId: file.project_id,
            fileId,
          });
          return;
        }

        const fileType = file.file_type as string;
        const normalizedType: TreeNodeType =
          fileType === "snippet" ? "material" : (fileType as TreeNodeType);

        setSelectedItem({
          id: file.id,
          title: file.title,
          type: normalizedType,
        });
        selectedFromQueryRef.current = fileId;
      } catch (err) {
        logger.warn("[ProjectEditor] Failed to load file from query param", {
          fileId,
          projectId,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    };

    void selectFileFromQuery();

    return () => {
      cancelled = true;
    };
  }, [currentProject, location.search, projectId, setSelectedItem]);

  // Ensure project exists in ProjectContext before rendering editor
  React.useEffect(() => {
    let cancelled = false;

    const ensureProject = async () => {
      if (!projectId) return;

      setCurrentProjectId(projectId);

      const exists = projects.some((p) => p.id === projectId);
      if (exists) {
        if (!cancelled) {
          setIsEnsuring(false);
          setNotFound(false);
        }
        return;
      }

      // Only attempt refresh once per projectId
      if (attemptedRefreshRef.current === projectId) {
        if (!cancelled) {
          setIsEnsuring(false);
          setNotFound(true);
        }
        return;
      }

      attemptedRefreshRef.current = projectId;
      if (!cancelled) setIsEnsuring(true);

      try {
        await refreshProjects();
      } finally {
        if (!cancelled) setIsEnsuring(false);
      }
    };

    void ensureProject();

    return () => {
      cancelled = true;
    };
  }, [projectId, projects, refreshProjects, setCurrentProjectId]);

  // Show loading state while projects are being fetched or ensuring project exists
  if (loading || isEnsuring) {
    return <PageLoader />;
  }

  // Show error state
  if (error) {
    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col items-center justify-center gap-4">
        <div className="text-[hsl(var(--error))]">{t('projectLoadFailed')}: {error}</div>
        <button onClick={() => refreshProjects()} className="btn btn-primary">
          {t('retry')}
        </button>
      </div>
    );
  }

  // Avoid immediate redirect: show a not-found screen after one refresh attempt
  if (!currentProject) {
    if (!notFound) {
      return <PageLoader />;
    }

    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col items-center justify-center gap-4">
        <div className="text-[hsl(var(--text-primary))]">{t('projectNotFound')}</div>
        <div className="flex gap-3">
          <button
            onClick={async () => {
              attemptedRefreshRef.current = null;
              setNotFound(false);
              setIsEnsuring(true);
              try {
                await refreshProjects();
              } finally {
                setIsEnsuring(false);
              }
            }}
            className="btn btn-primary"
          >
            {t('tryAgain')}
          </button>
          <button onClick={() => navigate("/dashboard", { replace: true })} className="btn btn-ghost">
            {t('backToDashboard')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <Layout left={<Sidebar />} middle={<Editor />} right={<ChatPanel />} />
  );
}

function App() {
  return (
    <HelmetProvider>
      <BrowserRouter>
        <SiteBoundary>
          <ThemeProvider>
            <FileSearchProvider>
              <AuthProvider>
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
                        <ProtectedProviders>
                          <ProtectedRoute>
                            <OnboardingPersonaPage />
                          </ProtectedRoute>
                        </ProtectedProviders>
                      }
                    />
                    <Route
                      path="/dashboard"
                      element={
                        <ProtectedProviders>
                          <ProtectedRoute>
                            <Dashboard />
                          </ProtectedRoute>
                        </ProtectedProviders>
                      }
                    >
                      <Route index element={<DashboardHome />} />
                      <Route path="projects" element={<DashboardProjects />} />
                      <Route path="materials" element={<MaterialsPage />} />
                      <Route path="skills" element={<SkillsPage />} />
                      <Route path="billing" element={<BillingPage />} />
                      <Route path="inspirations" element={<InspirationsPage />} />
                      <Route path="inspirations/:inspirationId" element={<InspirationDetailPage />} />
                    </Route>
                    <Route
                      path="/project/:projectId"
                      element={
                        <ProtectedProviders>
                          <ProtectedRoute>
                            <ProjectEditor />
                          </ProtectedRoute>
                        </ProtectedProviders>
                      }
                    />
                    <Route
                      path="/project/:projectId/dashboard"
                      element={
                        <ProtectedProviders>
                          <ProtectedRoute>
                            <ProjectDashboardPage />
                          </ProtectedRoute>
                        </ProtectedProviders>
                      }
                    />
                    <Route
                      path="/materials/:novelId"
                      element={
                        <ProtectedProviders>
                          <ProtectedRoute>
                            <MaterialDetailPage />
                          </ProtectedRoute>
                        </ProtectedProviders>
                      }
                    />

                    {/* Admin routes */}
                    <Route
                      path="/admin"
                      element={
                        <ProtectedProviders>
                          <AdminRoute>
                            <AdminLayout />
                          </AdminRoute>
                        </ProtectedProviders>
                      }
                    >
                      <Route index element={<AdminDashboard />} />
                      <Route path="users" element={<UserManagement />} />
                      <Route path="prompts" element={<PromptManagement />} />
                      <Route path="prompts/:projectType" element={<PromptEditor />} />
                      <Route path="skills" element={<SkillReviewPage />} />
                      <Route path="codes" element={<CodeManagement />} />
                      <Route path="subscriptions" element={<SubscriptionManagement />} />
                      <Route path="plans" element={<SubscriptionPlanManagement />} />
                      <Route path="audit-logs" element={<AuditLogPage />} />
                      <Route path="inspirations" element={<InspirationManagement />} />
                      <Route path="feedback" element={<FeedbackManagement />} />
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
              </AuthProvider>
            </FileSearchProvider>
          </ThemeProvider>
        </SiteBoundary>
      </BrowserRouter>
    </HelmetProvider>
  );
}

export default App;

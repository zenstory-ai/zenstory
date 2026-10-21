import { Navigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * Admin route wrapper - verifies superuser permission.
 *
 * Requirements:
 * - User must be authenticated (handled by ProtectedRoute wrapping this)
 * - User must have is_superuser = true
 * - Non-superusers are redirected to home with an access denied message
 */
export function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const { t } = useTranslation("admin");

  if (loading) {
    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="w-8 h-8 text-[hsl(var(--accent-primary))] animate-spin" />
          <p className="text-sm text-[hsl(var(--text-secondary))]">{t('admin.verifyingPermission')}</p>
        </div>
      </div>
    );
  }

  if (!user) {
    // Should not happen if wrapped by ProtectedRoute, but handle gracefully
    return <Navigate to="/login" replace />;
  }

  if (!user.is_superuser) {
    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col items-center justify-center gap-6 px-4">
        <div className="flex flex-col items-center gap-3">
          <div className="w-16 h-16 rounded-full bg-[hsl(var(--warning))] bg-opacity-20 flex items-center justify-center">
            <svg className="w-8 h-8 text-[hsl(var(--warning-text))]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h1 className="text-2xl font-semibold text-[hsl(var(--text-primary))]">
            {t('admin.insufficientPermission')}
          </h1>
          <p className="text-[hsl(var(--text-secondary))] text-center max-w-md">
            {t('admin.superuserRequired')}
          </p>
        </div>
        <button
          onClick={() => window.location.assign("/")}
          className="btn btn-primary"
        >
          {t('admin.backToHome')}
        </button>
      </div>
    );
  }

  return <>{children}</>;
}

export default AdminRoute;

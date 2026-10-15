import React, { useLayoutEffect, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AdminSidebar } from "./AdminSidebar";
import { AdminHeader } from "./AdminHeader";
import { useIsMobile } from "../../hooks/useMediaQuery";
import { useDialogInteractions } from "../ui/dialogFocus";

export const AdminLayout: React.FC = () => {
  const { t } = useTranslation("admin");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const isMobile = useIsMobile();
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const mobileSidebarRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  const menuOpen = isMobile && sidebarOpen;

  useLayoutEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [pathname]);

  useDialogInteractions({
    open: menuOpen,
    dialogRef: mobileSidebarRef,
    returnFocusRef: menuButtonRef,
    onEscape: () => setSidebarOpen(false),
    initialFocusSelector: 'nav [aria-current="page"]',
  });

  return (
    <div className="viewport-shell admin-shell ui-sans-headings fixed inset-0 flex h-dvh flex-col overflow-hidden bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))]">
      <AdminHeader
        onMenuClick={() => setSidebarOpen((open) => !open)}
        menuOpen={menuOpen}
        menuButtonRef={menuButtonRef}
      />

      <div className="flex flex-1 min-h-0 overflow-hidden">
        {!isMobile ? (
          <aside className="w-60 shrink-0 overflow-y-auto border-r border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] lg:w-64">
            <AdminSidebar />
          </aside>
        ) : (
          <>
            {sidebarOpen && (
              <div
                className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm md:hidden"
                onClick={() => setSidebarOpen(false)}
              />
            )}

            <div
              ref={mobileSidebarRef}
              id="admin-mobile-navigation"
              className={`fixed top-14 bottom-0 left-0 z-50 w-72 max-w-[88vw] border-r border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] shadow-2xl transition-transform duration-200 ease-out md:hidden ${
                sidebarOpen ? "translate-x-0" : "-translate-x-full"
              }`}
              role="dialog"
              aria-modal="true"
              aria-label={t("sidebar.title", "管理后台")}
              aria-hidden={!sidebarOpen}
              tabIndex={-1}
              inert={!sidebarOpen ? true : undefined}
            >
              <AdminSidebar onClose={() => setSidebarOpen(false)} />
            </div>
          </>
        )}

        <main ref={mainRef} inert={menuOpen ? true : undefined} aria-hidden={menuOpen ? true : undefined} className="flex-1 min-w-0 min-h-0 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1600px] px-3 py-4 sm:px-4 md:px-6 md:py-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
};

export default AdminLayout;

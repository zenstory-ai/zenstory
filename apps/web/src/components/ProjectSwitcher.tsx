/**
 * @fileoverview ProjectSwitcher component - Dropdown for switching between projects.
 *
 * This component provides a project selection dropdown with:
 * - **Project list**: Displays all user projects with search/filter capability
 * - **Project switching**: One-click navigation to a different project
 * - **Inline editing**: Rename projects directly from the dropdown
 * - **Project creation**: Create new projects inline without leaving context
 * - **Project deletion**: Remove projects with confirmation (except last project)
 *
 * The dropdown features:
 * - Responsive positioning (pinned inside the viewport on mobile, left-aligned on desktop)
 * - Keyboard navigation support (Enter to select, Escape to close)
 * - Click-outside-to-close functionality
 * - Loading states for async operations
 * - i18n support for all UI text
 *
 * @module components/ProjectSwitcher
 */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { ChevronDown, Plus, Folder, Check, Trash2, Pencil, X } from "lucide-react";
import { ApiError } from "../lib/apiClient";
import { handleApiError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { useProject } from "../contexts/ProjectContext";
import { useIsMobile } from "../hooks/useMediaQuery";
import { useAnimatedPresence } from "../hooks/useAnimatedPresence";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { Button } from "./ui/Button";
import { IconButton } from "./ui/IconButton";
import { getUpgradePromptDefinition } from "../config/upgradeExperience";

/**
 * Props for the ProjectSwitcher component.
 *
 * @interface ProjectSwitcherProps
 * @property {React.ReactNode} [children] - Optional children (not currently used)
 *
 * @description
 * The ProjectSwitcher is a self-contained component that retrieves project state
 * from ProjectContext. No props are required for basic usage.
 */
interface ProjectSwitcherProps {
  /** Optional children elements (reserved for future use) */
  children?: React.ReactNode;
}

/**
 * Project switcher dropdown component for navigating between projects.
 *
 * Provides a compact dropdown button that displays the current project name
 * and allows users to:
 * - Switch to any existing project
 * - Create a new project inline
 * - Edit project names inline
 * - Delete projects (with confirmation, except the last one)
 *
 * Features:
 * - **Search**: Filter projects by name with real-time search
 * - **Inline editing**: Click the pencil icon to rename a project
 * - **Keyboard support**: Enter to confirm edits, Escape to cancel
 * - **Responsive**: Centered dropdown on mobile, left-aligned on desktop
 * - **Visual feedback**: Current project highlighted with checkmark
 * - **Validation**: Prevents deletion of the last remaining project
 *
 * @param {ProjectSwitcherProps} props - Component props (currently unused)
 * @returns {React.ReactElement} The project switcher dropdown
 *
 * @example
 * // Basic usage (typically placed in the header)
 * import { ProjectSwitcher } from './components/ProjectSwitcher';
 *
 * export function Header() {
 *   return (
 *     <header className="flex items-center justify-between">
 *       <ProjectSwitcher />
 *       <UserMenu />
 *     </header>
 *   );
 * }
 *
 * @example
 * // With custom styling wrapper
 * <div className="flex items-center gap-4">
 *   <Logo />
 *   <ProjectSwitcher />
 * </div>
 */
export const ProjectSwitcher: React.FC<ProjectSwitcherProps> = () => {
  const { t } = useTranslation(['editor', 'common', 'dashboard', 'home']);
  const projectQuotaUpgradePrompt = getUpgradePromptDefinition("project_quota_blocked");
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const {
    projects,
    currentProject,
    currentProjectId,
    loading,
    error,
    refreshProjects,
    createProject,
    updateProject,
    deleteProject,
  } = useProject();

  const [isOpen, setIsOpen] = useState(false);
  const { isMounted: isDropdownMounted, isExiting: isDropdownExiting } = useAnimatedPresence(isOpen, 130);
  const [searchQuery, setSearchQuery] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [showProjectQuotaUpgradeModal, setShowProjectQuotaUpgradeModal] = useState(false);
  const [pendingDeleteProjectId, setPendingDeleteProjectId] = useState<string | null>(null);
  const [deletingProject, setDeletingProject] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const editInputRef = useRef<HTMLInputElement>(null);

  const closeDropdown = useCallback((returnFocus = false) => {
    setIsOpen(false);
    setSearchQuery("");
    setIsCreating(false);
    setNewProjectName("");
    setEditingProjectId(null);
    setEditingName("");
    if (returnFocus) {
      requestAnimationFrame(() => {
        triggerRef.current?.focus();
      });
    }
  }, []);

  /**
   * Effect: Close dropdown when clicking outside or pressing Escape.
   *
   * Registers listeners only while the dropdown is open, resets transient
   * search/editing state on close, and returns focus to the trigger on Escape.
   */
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        closeDropdown();
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || pendingDeleteProjectId !== null) return;
      if (editingProjectId) {
        event.preventDefault();
        setEditingProjectId(null);
        setEditingName("");
        return;
      }
      if (isCreating) {
        event.preventDefault();
        setIsCreating(false);
        setNewProjectName("");
        return;
      }
      event.preventDefault();
      closeDropdown(true);
    };

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [isOpen, editingProjectId, isCreating, pendingDeleteProjectId, closeDropdown]);

  /**
   * Effect: Focus edit input when editing starts.
   *
   * When editingProjectId changes to a non-null value, focuses and selects
   * the text in the edit input for immediate editing.
   */
  useEffect(() => {
    if (editingProjectId && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingProjectId]);

  /**
   * Handles selecting a project from the list.
   *
   * Switches to the specified project and navigates to its URL.
   * Does nothing if currently editing a project name.
   *
   * @param {string} projectId - The ID of the project to select
   */
  const handleSelectProject = (projectId: string) => {
    if (editingProjectId) return; // Don't switch while editing
    closeDropdown();
    // The route updates ProjectContext after navigation is confirmed. Mutating
    // it here would discard the current editor before the leave guard runs.
    navigate(`/project/${projectId}`);
  };

  /**
   * Initiates inline editing of a project name.
   *
   * Stops event propagation to prevent project selection while editing.
   *
   * @param {string} projectId - The ID of the project to edit
   * @param {string} currentName - The current name of the project
   * @param {React.MouseEvent} e - The click event
   */
  const handleStartEditing = (projectId: string, currentName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    setEditingProjectId(projectId);
    setEditingName(currentName);
  };

  /**
   * Saves the edited project name.
   *
   * Validates the new name and updates the project via the API.
   * Shows a toast error if the update fails (except for auth errors).
   */
  const handleSaveEdit = async () => {
    if (!editingProjectId || !editingName.trim()) {
      setEditingProjectId(null);
      setEditingName("");
      return;
    }

    try {
      await updateProject(editingProjectId, { name: editingName.trim() });
      setEditingProjectId(null);
      setEditingName("");
    } catch (error) {
      setEditingProjectId(null);
      setEditingName("");
      if (!(error instanceof ApiError && error.status === 401)) {
        toast.error(handleApiError(error));
      }
    }
  };

  /**
   * Cancels inline editing of a project name.
   */
  const handleCancelEdit = () => {
    setEditingProjectId(null);
    setEditingName("");
  };

  /**
   * Creates a new project with the entered name.
   *
   * Validates the project name, creates it via the API, closes the dropdown,
   * and navigates to the newly created project.
   * Shows a toast error if creation fails (except for auth errors).
   */
  const handleCreateProject = async () => {
    if (!newProjectName.trim()) return;

    try {
      const newProject = await createProject(newProjectName.trim());
      setNewProjectName("");
      setIsCreating(false);
      setIsOpen(false);
      // Navigate to the new project URL
      if (newProject.id) {
        navigate(`/project/${newProject.id}`);
      }
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        toast.error(handleApiError(error));
        if (
          error instanceof ApiError &&
          error.errorCode === "ERR_QUOTA_PROJECTS_EXCEEDED" &&
          projectQuotaUpgradePrompt.surface === "modal"
        ) {
          setShowProjectQuotaUpgradeModal(true);
        }
      }
    }
  };

  /**
   * Asks for confirmation before deleting a project.
   *
   * The last remaining project cannot be deleted; that is explained in a toast.
   *
   * @param {string} projectId - The ID of the project to delete
   * @param {React.MouseEvent} e - The click event
   */
  const handleRequestDeleteProject = (
    projectId: string,
    e: React.MouseEvent,
  ) => {
    e.stopPropagation();
    if (projects.length <= 1) {
      toast.info(t('editor:projectSwitcher.cannotDeleteLast'));
      return;
    }
    setPendingDeleteProjectId(projectId);
  };

  /**
   * Deletes the confirmed project. Shows a toast error if deletion fails.
   */
  const handleConfirmDeleteProject = async () => {
    if (!pendingDeleteProjectId || deletingProject) return;
    setDeletingProject(true);
    try {
      await deleteProject(pendingDeleteProjectId);
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        toast.error(handleApiError(error));
      }
    } finally {
      setDeletingProject(false);
      setPendingDeleteProjectId(null);
    }
  };

  /** Projects filtered by the current search query (case-insensitive) */
  const filteredProjects = projects.filter((project) =>
    project && project.name && project.name.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  return (
    <>
      <div className="relative min-w-0 max-w-full" ref={dropdownRef}>
      {/* Trigger Button */}
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={isOpen}
        aria-haspopup="true"
        onClick={() => (isOpen ? closeDropdown() : setIsOpen(true))}
        className="flex items-center gap-1.5 md:gap-2 min-h-11 md:min-h-0 px-2 py-1.5 md:px-3 hover:bg-[hsl(var(--bg-tertiary))] rounded-lg transition-colors group min-w-0 max-w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
      >
        <Folder size={16} className="text-[hsl(var(--text-secondary))] shrink-0" />
        <div className="text-left min-w-0 overflow-hidden">
            <div className="font-medium text-sm text-[hsl(var(--text-primary))] truncate">
              {currentProject?.name || (loading ? t('editor:projectSwitcher.loading') : error ? t('editor:projectSwitcher.loadFailed') : t('editor:projectSwitcher.noProjects'))}
            </div>
        </div>
        <ChevronDown
          size={14}
          className={`text-[hsl(var(--text-secondary))] motion-chevron shrink-0 ${
            isOpen ? "rotate-180" : ""
          }`}
        />
      </button>

      {/* Dropdown Menu */}
      {isDropdownMounted && (
        <div
          aria-hidden={isDropdownExiting ? true : undefined}
          inert={isDropdownExiting ? true : undefined}
          className={`flex flex-col bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl shadow-lg z-50 overflow-hidden ${
            isDropdownExiting ? "motion-popover-exit pointer-events-none" : "motion-popover-enter"
          } ${
            isMobile
              // Pinned to the viewport: an ancestor-relative, translated panel
              // spilled off a 390px screen when the title was long.
              ? 'fixed left-4 right-4 top-12 origin-top'
              : 'absolute top-full mt-2 left-0 w-80 max-w-[calc(100vw-2rem)] origin-top-left'
          }`}
          style={{
            maxHeight: "calc(var(--app-viewport-height) - 100px)",
            ...(isMobile ? { top: "calc(var(--app-viewport-top) + 48px)" } : {}),
          }}
        >
          {/* Search Input — hidden when there is 0 or 1 project; never hijack mobile keyboard on open */}
          {(projects.length > 1 || searchQuery.length > 0) && (
            <div className="shrink-0 p-3">
              <div className="relative">
                <input
                  ref={searchInputRef}
                  type="text"
                  role="searchbox"
                  aria-label={t('editor:projectSwitcher.searchPlaceholder')}
                  placeholder={t('editor:projectSwitcher.searchPlaceholder')}
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape" && searchQuery && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229) {
                      e.preventDefault();
                      e.stopPropagation();
                      setSearchQuery("");
                    }
                  }}
                  className={`w-full min-w-0 bg-[hsl(var(--bg-tertiary))] rounded-lg pl-3 py-2 text-sm text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.3)] transition-[box-shadow,background-color] duration-[var(--duration-fast)] ease-[var(--ease-standard)] ${searchQuery ? "pr-9" : "pr-3"}`}
                  autoFocus={!isMobile}
                />
                {searchQuery && (
                  <button
                    type="button"
                    onClick={() => {
                      setSearchQuery("");
                      searchInputRef.current?.focus();
                    }}
                    aria-label={t('common:clearSearch')}
                    title={t('common:clearSearch')}
                    className="absolute right-1 top-1/2 -translate-y-1/2 p-1.5 rounded-md text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors"
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Project List */}
          <div className="min-h-0 max-h-96 overflow-y-auto">
            {loading ? (
              <div className="p-8 text-center text-[hsl(var(--text-secondary))] text-sm">
                {t('editor:projectSwitcher.loading')}
              </div>
            ) : error && projects.length === 0 ? (
              <div className="p-6 text-center text-sm space-y-2">
                <p role="alert" className="text-[hsl(var(--error-text))]">{t('editor:projectSwitcher.loadFailed')}</p>
                <button
                  type="button"
                  onClick={() => void refreshProjects()}
                  className="text-xs font-medium text-[hsl(var(--accent-primary))] hover:underline"
                >
                  {t('common:retry')}
                </button>
              </div>
            ) : filteredProjects.length === 0 ? (
              <div className="p-8 text-center text-[hsl(var(--text-secondary))] text-sm">
                {searchQuery ? t('editor:projectSwitcher.noResults') : t('editor:projectSwitcher.noProjects')}
              </div>
            ) : (
              <div className="py-1">
                {filteredProjects.map((project) => (
                  <div
                    key={project.id}
                    className={`w-full flex items-start gap-2 px-3 py-2.5 transition-colors group ${
                      project.id === currentProjectId
                        ? "bg-[hsl(var(--accent-primary)/0.08)]"
                        : "hover:bg-[hsl(var(--bg-tertiary))]"
                    }`}
                  >
                    {editingProjectId === project.id ? (
                      <div className="flex flex-1 min-w-0 items-center gap-3">
                        <Folder
                          size={18}
                          className={`shrink-0 ${
                            project.id === currentProjectId
                              ? "text-[hsl(var(--accent-primary))]"
                              : "text-[hsl(var(--text-secondary))]"
                          }`}
                        />
                        <input
                          ref={editInputRef}
                          type="text"
                          value={editingName}
                          onChange={(e) => setEditingName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.nativeEvent.isComposing || e.keyCode === 229) return;
                            if (e.key === "Enter") handleSaveEdit();
                            if (e.key === "Escape") {
                              e.stopPropagation();
                              handleCancelEdit();
                            }
                          }}
                          onBlur={handleSaveEdit}
                          className="flex-1 min-w-0 bg-[hsl(var(--bg-primary))] border border-[hsl(var(--accent-primary))] rounded px-2 py-0.5 text-sm text-[hsl(var(--text-primary))] focus:outline-none"
                        />
                      </div>
                    ) : (
                      <>
                        {/* Selecting a project is a native button, sibling of Rename/Delete actions. */}
                        <button
                          type="button"
                          aria-current={project.id === currentProjectId ? "true" : undefined}
                          onClick={() =>
                            project.id && handleSelectProject(project.id)
                          }
                          className="flex flex-1 min-w-0 items-start gap-3 text-left rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
                        >
                          <Folder
                            size={18}
                            aria-hidden="true"
                            className={`mt-0.5 shrink-0 ${
                              project.id === currentProjectId
                                ? "text-[hsl(var(--accent-primary))]"
                                : "text-[hsl(var(--text-secondary))]"
                            }`}
                          />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
                              {project.name}
                            </div>
                            {project.description && (
                              <div className="text-xs text-[hsl(var(--text-secondary))] mt-0.5 truncate">
                                {project.description}
                              </div>
                            )}
                          </div>
                        </button>
                        <div className="flex shrink-0 items-center gap-0.5">
                          {project.id === currentProjectId && (
                            <Check
                              size={14}
                              aria-hidden="true"
                              className="text-[hsl(var(--accent-primary))] shrink-0 mr-0.5"
                            />
                          )}
                          <IconButton
                            onClick={(e) =>
                              project.id && handleStartEditing(project.id, project.name, e)
                            }
                            // Always visible: full strength on touch (no hover there), dimmed on
                            // desktop until the row is hovered or the button is focused.
                            className={
                              isMobile
                                ? "opacity-100"
                                : "opacity-50 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
                            }
                            label={t('editor:projectSwitcher.editName')}
                            icon={<Pencil size={12} />}
                            data-testid="project-switcher-rename"
                          />
                          <IconButton
                            onClick={(e) =>
                              project.id && handleRequestDeleteProject(project.id, e)
                            }
                            aria-disabled={projects.length <= 1 || undefined}
                            // Hidden on desktop until hover or keyboard focus; always shown on touch.
                            className={
                              projects.length <= 1
                                ? isMobile
                                  ? "opacity-40"
                                  : "opacity-0 group-hover:opacity-40 focus-visible:opacity-40 [@media(hover:none)]:opacity-40"
                                : isMobile
                                  ? "opacity-100"
                                  : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
                            }
                            tone="danger"
                            label={t('editor:projectSwitcher.deleteProject')}
                            icon={<Trash2 size={14} />}
                          />
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Footer - Create Project */}
          <div className="shrink-0 p-2 bg-[hsl(var(--bg-tertiary)/0.3)]">
            {isCreating ? (
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder={t('editor:projectSwitcher.projectNamePlaceholder')}
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
                    if (e.key === "Enter") handleCreateProject();
                    if (e.key === "Escape") {
                      e.stopPropagation();
                      setIsCreating(false);
                      setNewProjectName("");
                    }
                  }}
                  className="flex-1 bg-[hsl(var(--bg-primary))] border border-[hsl(var(--border-color))] rounded-lg px-3 py-2 text-sm text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))]"
                  autoFocus
                />
                <Button
                  type="button"
                  size={isMobile ? 'touch' : 'sm'}
                  onClick={handleCreateProject}
                  disabled={!newProjectName.trim()}
                >
                  {t('editor:projectSwitcher.create')}
                </Button>
              </div>
            ) : (
              <Button
                type="button"
                variant="secondary"
                size={isMobile ? 'touch' : 'md'}
                onClick={() => setIsCreating(true)}
                leftIcon={<Plus size={14} />}
                className="w-full"
              >
                {t('editor:projectSwitcher.createProject')}
              </Button>
            )}
          </div>
        </div>
      )}
      </div>

      <ConfirmDialog
        open={pendingDeleteProjectId !== null}
        onClose={() => {
          if (!deletingProject) setPendingDeleteProjectId(null);
        }}
        onConfirm={handleConfirmDeleteProject}
        title={t('editor:projectSwitcher.deleteProject')}
        message={t('editor:projectSwitcher.confirmDelete')}
        variant="danger"
        loading={deletingProject}
        confirmLabel={t('common:delete')}
        cancelLabel={t('common:cancel')}
      />

      <UpgradePromptModal
        open={showProjectQuotaUpgradeModal}
        onClose={() => setShowProjectQuotaUpgradeModal(false)}
        promptId="project_quota_blocked"
        title={t('editor:projectSwitcher.projectQuotaExceededTitle', {
          defaultValue: '项目数量已达上限',
        })}
        description={t('editor:projectSwitcher.projectQuotaExceededDesc', {
          defaultValue: '可以先删除不再需要的项目，或开通 Pro，项目不限。',
        })}
        primaryLabel={t('dashboard:billing.ctaUpgradePro', '开通 Pro')}
        secondaryLabel={t('home:pricingTeaser.viewPricing', '查看套餐权益')}
      />
    </>
  );
};

export default ProjectSwitcher;

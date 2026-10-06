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
 * - Responsive positioning (centered on mobile, left-aligned on desktop)
 * - Keyboard navigation support (Enter to select, Escape to close)
 * - Click-outside-to-close functionality
 * - Loading states for async operations
 * - i18n support for all UI text
 *
 * @module components/ProjectSwitcher
 */

import React, { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { ChevronDown, Plus, Folder, Check, Trash2, Pencil } from "lucide-react";
import { ApiError } from "../lib/apiClient";
import { handleApiError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { useProject } from "../contexts/ProjectContext";
import { useIsMobile } from "../hooks/useMediaQuery";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";

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
    switchProject,
    createProject,
    updateProject,
    deleteProject,
  } = useProject();

  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [showProjectQuotaUpgradeModal, setShowProjectQuotaUpgradeModal] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const editInputRef = useRef<HTMLInputElement>(null);

  /**
   * Effect: Close dropdown when clicking outside.
   *
   * Registers a mousedown event listener to detect clicks outside the dropdown.
   * When detected, closes the dropdown and resets all editing states.
   */
  useEffect(() => {
    /**
     * Handles click events outside the dropdown to close it.
     * @param {MouseEvent} event - The mousedown event
     */
    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
        setIsCreating(false);
        setNewProjectName("");
        setEditingProjectId(null);
        setEditingName("");
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

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
    switchProject(projectId);
    setIsOpen(false);
    // Navigate to the new project URL
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
   * Deletes a project after confirmation.
   *
   * Prevents deletion of the last remaining project. Shows a confirmation
   * dialog before deletion. Shows a toast error if deletion fails.
   *
   * @param {string} projectId - The ID of the project to delete
   * @param {React.MouseEvent} e - The click event
   */
  const handleDeleteProject = async (
    projectId: string,
    e: React.MouseEvent,
  ) => {
    e.stopPropagation();
    if (projects.length <= 1) {
      alert(t('editor:projectSwitcher.cannotDeleteLast'));
      return;
    }
    if (confirm(t('editor:projectSwitcher.confirmDelete'))) {
      try {
        await deleteProject(projectId);
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 401)) {
          toast.error(handleApiError(error));
        }
      }
    }
  };

  /** Projects filtered by the current search query (case-insensitive) */
  const filteredProjects = projects.filter((project) =>
    project && project.name && project.name.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  return (
    <>
      <div className="relative" ref={dropdownRef}>
      {/* Trigger Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 md:gap-2 px-2 py-1.5 md:px-3 hover:bg-[hsl(var(--bg-tertiary))] rounded-lg transition-colors group min-w-0"
      >
        <Folder size={16} className="text-[hsl(var(--text-secondary))] shrink-0" />
        <div className="text-left min-w-0 overflow-hidden">
            <div className="font-medium text-sm text-[hsl(var(--text-primary))] truncate">
              {currentProject?.name || t('editor:projectSwitcher.loading')}
            </div>
        </div>
        <ChevronDown
          size={14}
          className={`text-[hsl(var(--text-secondary))] transition-transform shrink-0 ${
            isOpen ? "rotate-180" : ""
          }`}
        />
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <div
          className={`absolute top-full mt-2 w-80 max-w-[calc(100vw-2rem)] bg-[hsl(var(--bg-secondary))] rounded-xl shadow-2xl z-50 overflow-hidden ${isMobile ? 'left-1/2 -translate-x-1/2' : 'left-0'}`}
          style={{ maxHeight: "calc(100vh - 100px)" }}
        >
          {/* Search Input */}
          <div className="p-3">
            <input
              type="text"
              placeholder={t('editor:projectSwitcher.searchPlaceholder')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full min-w-0 bg-[hsl(var(--bg-tertiary))] rounded-lg px-3 py-2 text-sm text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.3)] transition-all"
              autoFocus
            />
          </div>

          {/* Project List */}
          <div className="max-h-96 overflow-y-auto">
            {loading ? (
              <div className="p-8 text-center text-[hsl(var(--text-secondary))] text-sm">
                {t('editor:projectSwitcher.loading')}
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
                    onClick={() =>
                      project.id && handleSelectProject(project.id)
                    }
                    className="w-full flex items-start gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] transition-colors group cursor-pointer"
                  >
                    <Folder
                      size={18}
                      className={`mt-0.5 ${
                        project.id === currentProjectId
                          ? "text-[hsl(var(--accent-primary))]"
                          : "text-[hsl(var(--text-secondary))]"
                      }`}
                    />
                    <div className="flex-1 text-left min-w-0">
                      <div className="flex items-center justify-between gap-1">
                        {editingProjectId === project.id ? (
                          <input
                            ref={editInputRef}
                            type="text"
                            value={editingName}
                            onChange={(e) => setEditingName(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") handleSaveEdit();
                              if (e.key === "Escape") handleCancelEdit();
                            }}
                            onBlur={handleSaveEdit}
                            onClick={(e) => e.stopPropagation()}
                            className="flex-1 bg-[hsl(var(--bg-primary))] border border-[hsl(var(--accent-primary))] rounded px-2 py-0.5 text-sm text-[hsl(var(--text-primary))] focus:outline-none"
                          />
                        ) : (
                          <>
                            <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
                              {project.name}
                            </span>
                            <div className="flex items-center gap-0.5">
                              <button
                                onClick={(e) =>
                                  project.id && handleStartEditing(project.id, project.name, e)
                                }
                                className="text-[hsl(var(--text-secondary))] opacity-0 group-hover:opacity-100 hover:text-[hsl(var(--accent-primary))] transition-all p-1"
                                title={t('editor:projectSwitcher.editName')}
                              >
                                <Pencil size={12} />
                              </button>
                              {project.id === currentProjectId && (
                                <Check
                                  size={14}
                                  className="text-[hsl(var(--accent-primary))] shrink-0"
                                />
                              )}
                            </div>
                          </>
                        )}
                      </div>
                      {project.description && editingProjectId !== project.id && (
                        <div className="text-xs text-[hsl(var(--text-secondary))] mt-0.5 truncate">
                          {project.description}
                        </div>
                      )}
                    </div>
                    {editingProjectId !== project.id && (
                      <button
                        onClick={(e) =>
                          project.id && handleDeleteProject(project.id, e)
                        }
                        className="text-[hsl(var(--text-secondary))] opacity-0 group-hover:opacity-100 hover:text-[hsl(var(--error))] transition-all p-1"
                        title={t('editor:projectSwitcher.deleteProject')}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Footer - Create Project */}
          <div className="p-2 bg-[hsl(var(--bg-tertiary)/0.3)]">
            {isCreating ? (
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder={t('editor:projectSwitcher.projectNamePlaceholder')}
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreateProject();
                    if (e.key === "Escape") {
                      setIsCreating(false);
                      setNewProjectName("");
                    }
                  }}
                  className="flex-1 bg-[hsl(var(--bg-primary))] border border-[hsl(var(--border-color))] rounded-lg px-3 py-2 text-sm text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))]"
                  autoFocus
                />
                <button
                  onClick={handleCreateProject}
                  disabled={!newProjectName.trim()}
                  className="px-3 py-2 bg-[hsl(var(--accent-primary))] rounded-lg text-sm text-white hover:bg-[hsl(var(--accent-dark))] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {t('editor:projectSwitcher.create')}
                </button>
              </div>
            ) : (
              <button
                onClick={() => setIsCreating(true)}
                className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-[hsl(var(--accent-primary)/0.15)] border border-[hsl(var(--accent-primary)/0.4)] rounded-lg text-sm text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.25)] hover:border-[hsl(var(--accent-primary)/0.6)] transition-all"
              >
                <Plus size={14} />
                {t('editor:projectSwitcher.createProject')}
              </button>
            )}
          </div>
        </div>
      )}
      </div>

      <UpgradePromptModal
        open={showProjectQuotaUpgradeModal}
        onClose={() => setShowProjectQuotaUpgradeModal(false)}
        source={projectQuotaUpgradePrompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={t('editor:projectSwitcher.projectQuotaExceededTitle', {
          defaultValue: '项目数量已达上限',
        })}
        description={t('editor:projectSwitcher.projectQuotaExceededDesc', {
          defaultValue: '升级套餐后可创建更多项目。',
        })}
        primaryLabel={t('dashboard:billing.ctaUpgradePro', '升级专业版')}
        onPrimary={() => {
          window.location.assign(
            buildUpgradeUrl(projectQuotaUpgradePrompt.billingPath, projectQuotaUpgradePrompt.source)
          );
        }}
        secondaryLabel={t('home:pricingTeaser.viewPricing', '查看套餐权益')}
        onSecondary={() => {
          window.location.assign(
            buildUpgradeUrl(projectQuotaUpgradePrompt.pricingPath, projectQuotaUpgradePrompt.source)
          );
        }}
      />
    </>
  );
};

export default ProjectSwitcher;

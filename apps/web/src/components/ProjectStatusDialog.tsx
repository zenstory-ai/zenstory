import React, { useState, useEffect, useCallback, useRef, useId } from "react";
import { Save, Brain, Loader2, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { inspirationsApi, projectApi } from "../lib/api";
import { subscribeProjectStatusUpdated } from "../lib/projectStatusEvents";
import type { Project, PatchProjectRequest } from "../types";
import { Modal } from "./ui/Modal";
import { Button } from "./ui/Button";
import { ApiError } from "../lib/apiClient";
import { adoptAllProjectProposals, displayProjectNotes, storedProjectNotes } from "../lib/projectNotes";
import { toast } from "../lib/toast";
import { logger } from "../lib/logger";
import { inspirationsConfig } from "../config/inspirations";

interface ProjectStatusDialogProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
}

export const ProjectStatusDialog: React.FC<ProjectStatusDialogProps> = ({
  isOpen,
  onClose,
  projectId,
}) => {
  const { t, i18n } = useTranslation('project', { keyPrefix: 'statusDialog' });
  const navigate = useNavigate();
  const fieldId = useId();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [submittingInspiration, setSubmittingInspiration] = useState(false);
  const [project, setProject] = useState<Project | null>(null);

  const [summary, setSummary] = useState("");
  const [writingStyle, setWritingStyle] = useState("");
  const [currentPhase, setCurrentPhase] = useState("");
  const [notes, setNotes] = useState("");
  const [notesConflict, setNotesConflict] = useState(false);
  const [latestNotes, setLatestNotes] = useState<string | null>(null);
  const [loadingLatestNotes, setLoadingLatestNotes] = useState(false);
  const [latestNotesFailed, setLatestNotesFailed] = useState(false);
  const notesBaseRef = useRef("");
  const notesInputRef = useRef<HTMLTextAreaElement>(null);
  const latestNotesReadRef = useRef<object | null>(null);

  const projectRef = useRef<Project | null>(null);
  const formValuesRef = useRef({
    summary: "",
    writingStyle: "",
    currentPhase: "",
    notes: "",
  });

  const ownerRef = useRef<{ projectId: string; loadSequence: number } | null>(null);
  const activeSaveRef = useRef<object | null>(null);

  useEffect(() => {
    const owner = isOpen && projectId ? { projectId, loadSequence: 0 } : null;
    ownerRef.current = owner;
    projectRef.current = null;
    formValuesRef.current = { summary: "", writingStyle: "", currentPhase: "", notes: "" };
    activeSaveRef.current = null;
    setProject(null);
    setSummary("");
    setWritingStyle("");
    setCurrentPhase("");
    setNotes("");
    notesBaseRef.current = "";
    setNotesConflict(false);
    setLatestNotes(null);
    setLoadingLatestNotes(false);
    setLatestNotesFailed(false);
    latestNotesReadRef.current = null;
    setSaving(false);
    setLoading(Boolean(owner));

    return () => {
      if (ownerRef.current === owner) {
        ownerRef.current = null;
        activeSaveRef.current = null;
      }
    };
  }, [isOpen, projectId]);

  useEffect(() => {
    projectRef.current = project;
  }, [project]);

  useEffect(() => {
    formValuesRef.current = {
      summary,
      writingStyle,
      currentPhase,
      notes,
    };
  }, [summary, writingStyle, currentPhase, notes]);

  const applyProjectToForm = useCallback(
    (data: Project, preserveDirty: boolean) => {
      const nextSummary = data.summary || "";
      const nextWritingStyle = data.writing_style || "";
      const nextCurrentPhase = data.current_phase || "";
      const nextNotes = data.notes || "";

      if (!preserveDirty || !projectRef.current) {
        setSummary(nextSummary);
        setWritingStyle(nextWritingStyle);
        setCurrentPhase(nextCurrentPhase);
        setNotes(nextNotes);
        notesBaseRef.current = nextNotes;
        return;
      }

      const prevProject = projectRef.current;
      const prevValues = formValuesRef.current;

      const summaryDirty = prevValues.summary.trim() !== (prevProject.summary || "");
      const writingStyleDirty = prevValues.writingStyle.trim() !== (prevProject.writing_style || "");
      const currentPhaseDirty = prevValues.currentPhase.trim() !== (prevProject.current_phase || "");
      const notesDirty = prevValues.notes.trim() !== notesBaseRef.current.trim();

      if (!summaryDirty) setSummary(nextSummary);
      if (!writingStyleDirty) setWritingStyle(nextWritingStyle);
      if (!currentPhaseDirty) setCurrentPhase(nextCurrentPhase);
      if (!notesDirty || prevValues.notes.trim() === nextNotes.trim()) {
        setNotes(nextNotes);
        notesBaseRef.current = nextNotes;
      }
    },
    []
  );

  const loadProject = useCallback(
    async (options?: { showLoading?: boolean; preserveDirty?: boolean }) => {
      const owner = ownerRef.current;
      if (!owner || owner.projectId !== projectId) return;
      const sequence = ++owner.loadSequence;
      const ownsLoad = () => ownerRef.current === owner &&
        owner.projectId === projectId && owner.loadSequence === sequence;
      const showLoading = options?.showLoading ?? false;
      const preserveDirty = options?.preserveDirty ?? false;

      if (showLoading) {
        setLoading(true);
      }

      try {
        const data = await projectApi.get(projectId);
        if (!ownsLoad()) return;
        applyProjectToForm(data, preserveDirty);
        setProject(data);
      } catch (err) {
        if (ownsLoad()) logger.error("Failed to load project:", err);
      } finally {
        if (ownsLoad()) setLoading(false);
      }
    },
    [applyProjectToForm, projectId]
  );

  // Load project data when dialog opens
  useEffect(() => {
    if (!isOpen || !projectId) return;
    void loadProject({ showLoading: true, preserveDirty: false });
  }, [isOpen, projectId, loadProject]);

  // Sync updates from chat tool results while dialog is open.
  useEffect(() => {
    if (!isOpen || !projectId) return;

    return subscribeProjectStatusUpdated((detail) => {
      if (detail.projectId !== projectId) return;
      void loadProject({ showLoading: false, preserveDirty: true });
    });
  }, [isOpen, projectId, loadProject]);

  const viewLatestNotes = async () => {
    const owner = ownerRef.current;
    if (!owner || latestNotesReadRef.current) return;
    const readToken = {};
    latestNotesReadRef.current = readToken;
    const ownsRead = () => ownerRef.current === owner && latestNotesReadRef.current === readToken;
    setLoadingLatestNotes(true);
    setLatestNotesFailed(false);
    try {
      const data = await projectApi.get(owner.projectId);
      if (ownsRead()) setLatestNotes(data.notes || "");
    } catch (err) {
      if (ownsRead()) {
        logger.error("Failed to load latest project notes:", err);
        setLatestNotesFailed(true);
      }
    } finally {
      if (ownsRead()) {
        latestNotesReadRef.current = null;
        setLoadingLatestNotes(false);
      }
    }
  };

  const useLatestNotes = () => {
    const owner = ownerRef.current;
    if (latestNotes === null || !owner || owner.projectId !== projectId) return;
    owner.loadSequence++;
    setNotes(latestNotes);
    notesBaseRef.current = latestNotes;
    setProject(current => current ? { ...current, notes: latestNotes } : current);
    setNotesConflict(false);
    setLatestNotes(null);
    notesInputRef.current?.focus();
  };

  const handleSave = async (options?: { notes: string; expectedNotes?: string; adopting?: boolean }) => {
    const owner = ownerRef.current;
    if (!owner || owner.projectId !== projectId || project?.id !== projectId || activeSaveRef.current ||
      (notesConflict && options?.expectedNotes === undefined)) return;
    const saveToken = {};
    activeSaveRef.current = saveToken;
    const ownsSave = () => ownerRef.current === owner &&
      owner.projectId === projectId && activeSaveRef.current === saveToken;

    setSaving(true);
    try {
      const nextSummary = summary.trim();
      const nextWritingStyle = writingStyle.trim();
      const nextCurrentPhase = currentPhase.trim();
      const notesOnly = options !== undefined;
      const nextNotes = (options?.notes ?? notes).trim();

      const patch: PatchProjectRequest = {};
      if (!notesOnly && (project.summary || "") !== nextSummary) patch.summary = nextSummary;
      if (!notesOnly && (project.writing_style || "") !== nextWritingStyle)
        patch.writing_style = nextWritingStyle;
      if (!notesOnly && (project.current_phase || "") !== nextCurrentPhase)
        patch.current_phase = nextCurrentPhase;
      if (notesOnly || (project.notes || "") !== nextNotes) {
        patch.notes = nextNotes;
        patch.expected_notes = options?.expectedNotes ?? notesBaseRef.current;
      }
      if (notesOnly) {
        setNotes(nextNotes);
        notesInputRef.current?.focus();
      }

      const updated = await projectApi.patch(projectId, patch);
      if (!ownsSave()) return;
      // A notes-only save leaves the dialog open; older GETs must not undo it.
      owner.loadSequence++;
      setProject(updated);
      if (patch.notes !== undefined) notesBaseRef.current = updated.notes || "";
      setNotesConflict(false);
      setLatestNotes(null);
      if (notesOnly) toast.success(t(options.adopting ? 'proposalsAdopted' : 'notesSaved'));
      else onClose();
    } catch (err) {
      if (ownsSave()) {
        logger.error("Failed to save project status:", err);
        if (err instanceof ApiError && err.status === 409) {
          setNotesConflict(true);
          setLatestNotes(null);
        }
        else toast.error(t('saveFailed'));
      }
    } finally {
      if (ownsSave()) {
        activeSaveRef.current = null;
        setSaving(false);
      }
    }
  };

  const handleSubmitInspiration = async () => {
    if (!projectId || !project) return;

    setSubmittingInspiration(true);
    try {
      const result = await inspirationsApi.submit({
        project_id: projectId,
        name: project.name,
        description: project.description || undefined,
      });

      toast.success(
        result.status === "approved"
          ? t('shareSuccessApproved')
          : t('shareSuccessPending'),
      );
    } catch (err) {
      logger.error("Failed to submit inspiration:", err);
      toast.error(t('shareFailed'));
    } finally {
      setSubmittingInspiration(false);
    }
  };

  const originalSummary = project?.summary || "";
  const originalWritingStyle = project?.writing_style || "";
  const originalCurrentPhase = project?.current_phase || "";
  const originalNotes = project?.notes || "";
  const adoptedNotes = adoptAllProjectProposals(notes);

  const hasChanges =
    summary.trim() !== originalSummary ||
    writingStyle.trim() !== originalWritingStyle ||
    currentPhase.trim() !== originalCurrentPhase ||
    notes.trim() !== originalNotes;

  const handleViewInspirationLibrary = () => {
    onClose();
    navigate('/dashboard/inspirations');
  };

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="lg"
      title={
        <div className="flex items-center gap-2">
          <Brain className="w-5 h-5 text-[hsl(var(--accent-primary))] shrink-0" />
          <span className="text-lg font-semibold leading-none text-[hsl(var(--text-primary))]">
            {t('title')}
          </span>
        </div>
      }
      footer={
        <div className="w-full flex flex-col sm:flex-row sm:flex-wrap sm:justify-end gap-2 sm:gap-3">
          {inspirationsConfig.enabled && (
            <>
              <button
                onClick={handleSubmitInspiration}
                disabled={submittingInspiration || loading || !project}
                className="w-full sm:w-auto px-4 py-2 bg-[hsl(var(--bg-secondary))] text-[hsl(var(--accent-primary))] border border-[hsl(var(--accent-primary)/0.35)] rounded-lg text-sm hover:bg-[hsl(var(--accent-primary)/0.08)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {submittingInspiration ? t('sharingInspiration') : t('shareAsInspiration')}
              </button>
              <button
                onClick={handleViewInspirationLibrary}
                className="w-full sm:w-auto px-4 py-2 bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))] border border-[hsl(var(--border-color))] rounded-lg text-sm hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
              >
                {t('viewInspirationLibrary')}
              </button>
            </>
          )}
          <Button variant="secondary" size="touch"
            onClick={onClose}
            className="w-full sm:w-auto"
          >
            {t('cancel')}
          </Button>
          <Button size="touch"
            onClick={() => void handleSave()}
            disabled={saving || loading || !hasChanges || notesConflict}
            className="w-full sm:w-auto"
            isLoading={saving}
            loadingText={t('saving')}
            leftIcon={<Save size={14} />}
          >
            {t('save')}
          </Button>
        </div>
      }
    >
      {loading ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-8 h-8 animate-spin text-[hsl(var(--accent-primary))]" />
        </div>
      ) : (
        <div className="space-y-3">
          {/* Info Banner */}
          <div className="flex items-start gap-2 px-2.5 py-2 bg-[hsl(var(--bg-tertiary))] rounded-md text-xs text-[hsl(var(--text-secondary))]">
            <Sparkles className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))] shrink-0 mt-0.5" />
            <span className="leading-relaxed">
              {t('infoBanner')}
            </span>
          </div>

          <div className="space-y-1.5">
            <label htmlFor={`${fieldId}-summary`} className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('projectSummary')}
            </label>
            <textarea
              id={`${fieldId}-summary`}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder={t('placeholders.summary')}
              rows={5}
              className="w-full px-3 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))] resize-y"
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor={`${fieldId}-style`} className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('writingStyle')}
            </label>
            <textarea
              id={`${fieldId}-style`}
              value={writingStyle}
              onChange={(e) => setWritingStyle(e.target.value)}
              placeholder={t('placeholders.writingStyle')}
              rows={2}
              className="w-full px-3 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))] resize-y"
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor={`${fieldId}-phase`} className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('currentPhase')}
            </label>
            <textarea
              id={`${fieldId}-phase`}
              value={currentPhase}
              onChange={(e) => setCurrentPhase(e.target.value)}
              placeholder={t('placeholders.currentPhase')}
              rows={2}
              className="w-full px-3 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))] resize-y"
            />
          </div>

          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label htmlFor={`${fieldId}-notes`} className="text-sm font-medium text-[hsl(var(--text-primary))]">
                {t('notes')}
              </label>
              {adoptedNotes !== null && (
                <Button variant="secondary" size="touch" disabled={saving || notesConflict}
                  onClick={() => void handleSave({ notes: adoptedNotes, adopting: true })}>
                  {t('adoptAllProposals')}
                </Button>
              )}
            </div>
            <textarea
              id={`${fieldId}-notes`}
              ref={notesInputRef}
              value={displayProjectNotes(notes, i18n?.language)}
              onChange={(e) => {
                const nextNotes = storedProjectNotes(e.target.value, i18n?.language);
                setNotes(nextNotes);
                if (nextNotes.trim() === (projectRef.current?.notes || "").trim()) {
                  notesBaseRef.current = projectRef.current?.notes || "";
                }
              }}
              placeholder={t('placeholders.notes')}
              rows={3}
              className="w-full px-3 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))] resize-y"
            />
            {notesConflict && (
              <div className="space-y-2">
                <p role="alert" className="text-sm text-[hsl(var(--error-text))]">{t('notesConflict')}</p>
                {latestNotes === null ? <>
                  {latestNotesFailed && <p role="alert" className="text-sm text-[hsl(var(--error-text))]">{t('latestNotesFailed')}</p>}
                  <Button variant="secondary" size="touch" isLoading={loadingLatestNotes}
                    onClick={() => void viewLatestNotes()}>{t('viewLatestNotes')}</Button>
                </> : <>
                  <label htmlFor={`${fieldId}-latest-notes`} className="block text-sm font-medium text-[hsl(var(--text-primary))]">{t('latestNotes')}</label>
                  <textarea id={`${fieldId}-latest-notes`} value={displayProjectNotes(latestNotes, i18n?.language)} readOnly rows={3}
                    className="w-full px-3 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg text-sm text-[hsl(var(--text-primary))] resize-y" />
                  <div className="flex flex-wrap gap-2">
                    <Button variant="secondary" size="touch" disabled={saving} onClick={useLatestNotes}>{t('useLatestNotes')}</Button>
                    <Button variant="secondary" size="touch" disabled={saving}
                      onClick={() => void handleSave({ notes, expectedNotes: latestNotes })}>{t('saveMyNotes')}</Button>
                  </div>
                </>}
              </div>
            )}
          </div>
        </div>
      )}

    </Modal>
  );
};

export default ProjectStatusDialog;

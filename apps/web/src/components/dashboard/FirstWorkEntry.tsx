import { useEffect, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AlertCircle, Book } from '../icons';
import { LoadingSpinner } from '../LoadingSpinner';
import { Button } from '../ui/Button';
import { useFirstWork } from '../../hooks/useFirstWork';
import { firstWorkApi, firstWorkQueryKey } from '../../lib/firstWorkApi';
import type { ProjectType } from '../../types';

export function FirstWorkEntry({ userId, preferredType, onCreated }: {
  userId: string | null; preferredType: ProjectType; onCreated: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation('dashboard');
  const navigate = useNavigate();
  const client = useQueryClient();
  const state = useFirstWork(userId);
  const attempted = useRef<string | null>(null);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const currentUser = useRef(userId);
  useEffect(() => { currentUser.current = userId; }, [userId]);
  const initialize = useMutation({
    mutationFn: ({ type, language }: { type: ProjectType; language: string; owner: string | null }) => firstWorkApi.initialize(type, language),
    retry: false,
    onSuccess: (result, variables) => {
      client.setQueryData(firstWorkQueryKey(variables.owner), result);
      if (mounted.current && currentUser.current === variables.owner && result.status === 'created' && result.available) void onCreated();
    },
  });
  const { mutate } = initialize;
  useEffect(() => {
    if (!userId || state.data?.status !== 'pending' || attempted.current === userId) return;
    attempted.current = userId;
    mutate({ type: preferredType, language: i18n.language, owner: userId });
  }, [userId, state.data?.status, mutate, preferredType, i18n.language]);

  const work = state.data;
  if (state.isError || initialize.isError) {
    return <div role="alert" className="mb-5 flex flex-col gap-4 rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
      <div className="flex min-w-0 items-start gap-3">
        <AlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-[hsl(var(--error-text))]" />
        <p className="text-sm leading-6 text-[hsl(var(--text-secondary))]">{t('firstWork.failed')}</p>
      </div>
      <Button variant="secondary" size="touch" className="shrink-0" onClick={() => {
        if (state.isError) void state.refetch();
        else mutate({ type: preferredType, language: i18n.language, owner: userId });
      }}>{t('common:retry')}</Button>
    </div>;
  }
  // Nothing while eligibility is unknown: most accounts are not in the rollout, and a
  // 「正在准备…」 flash on every dashboard load would announce work that never happens.
  if (state.isPending) return null;
  if (work?.status === 'pending') {
    return <div role="status" aria-busy="true" className="mb-5 flex items-center gap-3 rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-5 sm:p-6">
      <LoadingSpinner size="sm" className="shrink-0" />
      <p className="text-sm leading-6 text-[hsl(var(--text-secondary))]">{t('firstWork.preparing')}</p>
    </div>;
  }
  if (!work?.available || !work.static_guidance || work.first_manual_write_at || work.first_ai_requested_at || !work.project_id || !work.file_id) return null;
  return <div data-testid="first-work-entry" className="mb-5 flex flex-col gap-5 rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
    <div className="flex min-w-0 items-start gap-3">
      <Book aria-hidden="true" className="mt-0.5 h-6 w-6 shrink-0 text-[hsl(var(--accent-primary))]" />
      <div className="min-w-0"><h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">{t('firstWork.title')}</h2>
        <p className="mt-1 text-sm leading-6 text-[hsl(var(--text-secondary))]">{t('firstWork.description')}</p></div>
    </div>
    <Button variant="secondary" size="touch" className="shrink-0" onClick={() => navigate(`/project/${work.project_id}?file=${work.file_id}`)}>{t('firstWork.open')}</Button>
  </div>;
}

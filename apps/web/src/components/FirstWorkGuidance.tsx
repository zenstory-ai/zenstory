import { useContext, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { QueryClientContext, useQueryClient } from '@tanstack/react-query';
import { useFirstWork, needsStaticFirstWorkGuidance } from '../hooks/useFirstWork';
import { firstWorkApi, firstWorkQueryKey } from '../lib/firstWorkApi';
import { useMobileLayout } from '../contexts/MobileLayoutContext';
import { Button } from './ui/Button';

type WorkFile = { userId: string | null; projectId: string | null; fileId: string };

/** Only a resolved project route is an open, including after its original manuscript was deleted. */
function OpenTracker({ userId, projectId }: Pick<WorkFile, 'userId' | 'projectId'>) {
  const { data } = useFirstWork(userId);
  const client = useQueryClient();
  const attempted = useRef<string | null>(null);
  const matches = Boolean(projectId && data?.project_id === projectId);
  useEffect(() => {
    const scope = `${userId}:${projectId}`;
    if (!matches || data?.opened_at || !userId || attempted.current === scope) return;
    attempted.current = scope;
    // No automatic retry; this idempotent metadata write never invokes a model.
    void firstWorkApi.open().then(() => {
      // Re-read instead of replacing newer AI/manual progress with an older open response.
      void client.invalidateQueries({ queryKey: firstWorkQueryKey(userId) });
    }).catch(() => {});
  }, [matches, data?.opened_at, userId, projectId, client]);
  return null;
}

export function FirstWorkOpenTracker(props: Pick<WorkFile, 'userId' | 'projectId'>) {
  const client = useContext(QueryClientContext);
  return client ? <OpenTracker {...props} /> : null;
}

function Guidance({ userId, projectId, fileId, focusEditor }: WorkFile & { focusEditor: () => void }) {
  const { t } = useTranslation('editor');
  const { data } = useFirstWork(userId);
  const { isMobile, switchToChat, focusChatInput } = useMobileLayout();
  const matchesFile = data?.available && data.project_id === projectId && data.file_id === fileId;
  if (!matchesFile || !needsStaticFirstWorkGuidance(data, projectId)) return null;
  return <div data-testid="first-work-guidance" className="mb-5 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4">
    <p className="text-sm leading-6 text-[hsl(var(--text-secondary))]">{t('firstWork.hint')}</p>
    <div className="mt-3 flex flex-col items-stretch gap-2 sm:flex-row sm:flex-wrap">
      <Button size="touch" className="max-w-full whitespace-normal! text-left" variant="secondary" onClick={focusEditor}>{t('firstWork.paste')}</Button>
      <Button size="touch" className="max-w-full whitespace-normal! text-left" variant="secondary" onClick={isMobile ? switchToChat : focusChatInput}>{t('firstWork.ai')}</Button>
    </div>
  </div>;
}

export function FirstWorkGuidance(props: WorkFile & { focusEditor: () => void }) {
  // Keep the embeddable editor usable outside the app's query provider.
  const client = useContext(QueryClientContext);
  return client ? <Guidance {...props} /> : null;
}

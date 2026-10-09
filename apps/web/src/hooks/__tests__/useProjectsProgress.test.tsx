/**
 * Leaving the editor right after typing: the editor's unmount flush and the
 * dashboard's progress request start in the same commit. The card must show
 * the count after that save, not before it.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getProgress: vi.fn() }));
vi.mock('../../lib/api', () => ({ projectApi: { getProgress: api.getProgress } }));
vi.mock('../../lib/writingStatsApi', () => ({ writingStatsApi: { recordStats: vi.fn(async () => ({})) } }));
vi.mock('../../contexts/TextQuoteContext', () => ({ useTextQuote: () => ({ addQuote: vi.fn() }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { SimpleEditor } from '../../components/SimpleEditor';
import type { SaveResult } from '../../components/SimpleEditor';
import { notifyEditorContentSaved } from '../../lib/editorSaveTracker';
import { useProjectsProgress } from '../useProjectsProgress';

const server = { words: 100 };
// The editor page registers its flush like Editor does; the unmount cleanup is what saves on leave.
const registerFlush = () => {};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

function Cards() {
  const progress = useProjectsProgress(1);
  return <span data-testid="card-words">{progress.get('p1')?.word_count ?? '-'}</span>;
}

function EditorPage({ onSave }: { onSave: () => Promise<SaveResult> }) {
  const [content, setContent] = useState('旧的正文');
  const [title, setTitle] = useState('第1章');
  return (
    <SimpleEditor
      fileId="f1"
      projectId="p1"
      fileType="draft"
      baseUpdatedAt="t0"
      title={title}
      content={content}
      onTitleChange={setTitle}
      onContentChange={setContent}
      onSave={onSave}
      onFlushReady={registerFlush}
    />
  );
}

function App({ page, onSave }: { page: 'editor' | 'dashboard'; onSave: () => Promise<SaveResult> }) {
  return page === 'editor' ? <EditorPage onSave={onSave} /> : <Cards />;
}

beforeEach(() => {
  server.words = 100;
  api.getProgress.mockReset();
  api.getProgress.mockImplementation(async () => [
    { project_id: 'p1', written_units: 1, word_count: server.words, framework_ready: false },
  ]);
});
afterEach(() => cleanup());

describe('useProjectsProgress after leaving the editor', () => {
  it('waits for the editor flush before asking for progress, so the card shows the new count', async () => {
    const save = deferred<SaveResult>();
    const onSave = vi.fn(() => save.promise);
    const view = render(<App page="editor" onSave={onSave} />);
    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), {
      target: { value: '旧的正文，又补了二十个字的新内容在这里' },
    });

    // Leave within the autosave debounce: the unmount flush starts the save.
    view.rerender(<App page="dashboard" onSave={onSave} />);
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(api.getProgress).not.toHaveBeenCalled();
    expect(screen.getByTestId('card-words')).toHaveTextContent('-');

    // The save lands; only now is the progress read.
    server.words = 120;
    await act(async () => { save.resolve({ outcome: 'saved', updatedAt: 't1' }); });
    await waitFor(() => expect(screen.getByTestId('card-words')).toHaveTextContent('120'));
    expect(api.getProgress).toHaveBeenCalled();
  });

  it('refetches when the editor reports a completed save while the dashboard is open', async () => {
    render(<Cards />);
    await waitFor(() => expect(screen.getByTestId('card-words')).toHaveTextContent('100'));
    server.words = 140;
    act(() => notifyEditorContentSaved('p1'));
    await waitFor(() => expect(screen.getByTestId('card-words')).toHaveTextContent('140'));
  });
});

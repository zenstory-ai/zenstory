import { createContext } from 'react';
import type { FileEditUndoTarget } from '../types';

/**
 * AI edits the author undid in this chat (live). After a refresh the edit card reads
 * `undo.undone`, which the server records on the card when the undo succeeded.
 */
export const UndoneEditsContext = createContext<ReadonlySet<string>>(new Set());

/** One edit is identified by its file, the version before it and the token right after it. */
export function undoneEditKey(target: Pick<FileEditUndoTarget, 'fileId' | 'beforeVersionNumber' | 'expectedAfterUpdatedAt'>): string {
  return `${target.fileId}\n${target.beforeVersionNumber}\n${target.expectedAfterUpdatedAt}`;
}

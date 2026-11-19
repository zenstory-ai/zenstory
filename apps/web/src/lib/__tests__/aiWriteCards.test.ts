import { describe, expect, it } from 'vitest';
import type { ToolCall } from '../../types';
import { collectUndoCardRefs, readFileEditUndoTarget, undoBlockedByLaterWrite } from '../aiWriteCards';
import { undoneEditKey } from '../undoneEdits';

const HASH = 'a'.repeat(64);

const editCard = (id: string, fileId: string, before: number, after: string, extra: Record<string, unknown> = {}): ToolCall => ({
  id,
  tool_name: 'edit_file',
  arguments: {},
  status: 'success',
  result: { id: fileId, undo: { before_version_number: before, expected_after_updated_at: after, ...extra } },
});

const replaceCard = (id: string, fileId: string, before: number, after: string): ToolCall => ({
  id,
  tool_name: 'write_chapter',
  arguments: {},
  status: 'success',
  result: {
    data: {
      id: fileId,
      action: 'replaced',
      body_saved: true,
      mutation_applied: true,
      undo: { before_version_number: before, expected_after_updated_at: after, expected_after_content_sha256: HASH },
    },
  },
});

const key = (fileId: string, before: number, after: string) =>
  undoneEditKey({ fileId, beforeVersionNumber: before, expectedAfterUpdatedAt: after });

describe('undoBlockedByLaterWrite', () => {
  const replace = replaceCard('t1', 'f1', 2, '2026-11-16T10:00:00.000001+00:00');
  const edit = editCard('t2', 'f1', 3, '2026-11-16T10:00:00.000002+00:00');

  it('blocks the earlier card while a later write to the same file is in place', () => {
    const blocked = undoBlockedByLaterWrite(collectUndoCardRefs([{ toolCalls: [replace, edit] }], new Set()));
    expect([...blocked]).toEqual([key('f1', 2, '2026-11-16T10:00:00.000001+00:00')]);
  });

  it('orders by the post-edit token, not by where the cards appear', () => {
    const blocked = undoBlockedByLaterWrite(collectUndoCardRefs([{ toolResults: [edit] }, { toolCalls: [replace] }], new Set()));
    expect(blocked.has(key('f1', 2, '2026-11-16T10:00:00.000001+00:00'))).toBe(true);
    expect(blocked.has(key('f1', 3, '2026-11-16T10:00:00.000002+00:00'))).toBe(false);
  });

  it('unblocks the earlier card once the later one is undone here or recorded undone', () => {
    const live = new Set([key('f1', 3, '2026-11-16T10:00:00.000002+00:00')]);
    expect(undoBlockedByLaterWrite(collectUndoCardRefs([{ toolCalls: [replace, edit] }], live)).size).toBe(0);

    const recorded = editCard('t2', 'f1', 3, '2026-11-16T10:00:00.000002+00:00', { undone: true });
    expect(undoBlockedByLaterWrite(collectUndoCardRefs([{ toolCalls: [replace, recorded] }], new Set())).size).toBe(0);
  });

  it('compares tokens across time zones', () => {
    const earlier = editCard('a', 'f1', 1, '2026-11-16T18:00:00+08:00');
    const later = editCard('b', 'f1', 2, '2026-11-16T10:00:01Z');
    const blocked = undoBlockedByLaterWrite(collectUndoCardRefs([{ toolCalls: [later, earlier] }], new Set()));
    expect([...blocked]).toEqual([key('f1', 1, '2026-11-16T18:00:00+08:00')]);
  });

  it('does not block across files, nor for a later write without an undo anchor', () => {
    const otherFile = editCard('t3', 'f2', 5, '2026-11-16T11:00:00Z');
    const noAnchor: ToolCall = { id: 't4', tool_name: 'edit_file', arguments: {}, status: 'success', result: { id: 'f1' } };
    const failed: ToolCall = { ...editCard('t5', 'f1', 9, '2026-11-16T12:00:00Z'), status: 'error' };
    const blocked = undoBlockedByLaterWrite(collectUndoCardRefs(
      [{ toolCalls: [replace, otherFile] }, { displayItems: [{ type: 'tool_calls', toolCalls: [noAnchor, failed] }] }],
      new Set(),
    ));
    expect(blocked.size).toBe(0);
  });

  it('ignores a replacement that has not saved its text yet', () => {
    const armed: ToolCall = { ...replaceCard('t6', 'f1', 4, '2026-11-16T13:00:00Z') };
    (armed.result!.data as Record<string, unknown>).body_saved = undefined;
    const blocked = undoBlockedByLaterWrite(collectUndoCardRefs([{ toolCalls: [replace, armed] }], new Set()));
    expect(blocked.size).toBe(0);
  });
});

describe('readFileEditUndoTarget', () => {
  it('carries the content hash only when it is a SHA-256 hex digest', () => {
    const anchor = { before_version_number: 2, expected_after_updated_at: '2026-11-16T10:00:00Z' };
    expect(readFileEditUndoTarget({ id: 'f1', undo: { ...anchor, expected_after_content_sha256: HASH } }, undefined, undefined))
      .toEqual({ fileId: 'f1', beforeVersionNumber: 2, expectedAfterUpdatedAt: '2026-11-16T10:00:00Z', expectedAfterContentSha256: HASH });
    expect(readFileEditUndoTarget({ id: 'f1', undo: { ...anchor, expected_after_content_sha256: 'XYZ' } }, undefined, undefined))
      .toEqual({ fileId: 'f1', beforeVersionNumber: 2, expectedAfterUpdatedAt: '2026-11-16T10:00:00Z' });
  });
});

import { describe, expect, it } from 'vitest';
import { applyPendingEditsToDiffs, buildParagraphReviewData } from '../diffReview';
import { rebaseLocalEdits } from '../rebaseLocalEdits';

/** What finishing the comparison saves when the author just presses finish. */
function finishWithDefaults(server: string, proposal: string, conflictEditIds: string[]) {
  const { diffs, pendingEdits } = buildParagraphReviewData(server, proposal);
  const edits = pendingEdits.map((edit) => (
    conflictEditIds.includes(edit.id) ? { ...edit, status: 'rejected' as const } : edit
  ));
  return applyPendingEditsToDiffs(diffs, edits);
}

const V1 = '中午十二点，老周还在摊上。\n\n他看了一眼手表。';
const V2 = '第二天中午十二点，老周还在摊上。\n\n他看了一眼手表。';

describe('rebaseLocalEdits', () => {
  it('replays an author sentence on top of the AI change to another paragraph', () => {
    const result = rebaseLocalEdits(V1, `${V1}作者补的一句。`, V2);
    expect(result.merged).toBe(`${V2}作者补的一句。`);
    expect(result.proposal).toBe(result.merged);
    expect(result.conflictEditIds).toEqual([]);
    expect(finishWithDefaults(V2, result.proposal, result.conflictEditIds)).toBe(`${V2}作者补的一句。`);
  });

  it('merges edits at different places of the same paragraph', () => {
    const base = '老周还在摊上，他看了一眼手表，天快黑了。';
    const server = '第二天，老周还在摊上，他看了一眼手表，天快黑了。';
    const local = '老周还在摊上，他看了一眼手表，天快黑了。风很大。';
    const result = rebaseLocalEdits(base, local, server);
    expect(result.merged).toBe('第二天，老周还在摊上，他看了一眼手表，天快黑了。风很大。');
    expect(result.conflictEditIds).toEqual([]);
  });

  it('keeps the AI text by default where both sides rewrote the same words, and shows the author side', () => {
    const base = '第一段。\n\n老周看了一眼手表。\n\n第三段。';
    const server = '第一段。\n\n老周盯着那块旧表看了很久。\n\n第三段。';
    const local = '第一段。\n\n老周没有看表。\n\n第三段。作者加的。';
    const result = rebaseLocalEdits(base, local, server);
    expect(result.merged).toBe('第一段。\n\n老周盯着那块旧表看了很久。\n\n第三段。作者加的。');
    expect(result.proposal).toBe('第一段。\n\n老周没有看表。\n\n第三段。作者加的。');
    expect(result.conflictEditIds).toHaveLength(1);
    // Finishing without choosing keeps the AI's paragraph and the author's clean addition.
    expect(finishWithDefaults(server, result.proposal, result.conflictEditIds)).toBe(result.merged);
  });

  it('returns the server copy when the author has nothing beyond it', () => {
    expect(rebaseLocalEdits(V1, V1, V2)).toEqual({ merged: V2, proposal: V2, conflictEditIds: [] });
    expect(rebaseLocalEdits(V1, V2, V2)).toEqual({ merged: V2, proposal: V2, conflictEditIds: [] });
  });

  it('keeps an author deletion of a paragraph the AI did not touch', () => {
    const base = 'A段。\n\nB段。\n\nC段。';
    const server = 'A段改。\n\nB段。\n\nC段。';
    const local = 'A段。\n\nC段。';
    expect(rebaseLocalEdits(base, local, server).merged).toBe('A段改。\n\nC段。');
  });
});

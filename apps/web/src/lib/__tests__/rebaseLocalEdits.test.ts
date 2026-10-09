import { describe, expect, it } from 'vitest';
import { applyPendingEditsToDiffs, buildParagraphReviewData } from '../diffReview';
import { rebaseLocalEdits } from '../rebaseLocalEdits';

/**
 * What finishing the comparison (the author's text on the left, the merge on
 * the right) saves: conflicts start rejected, `decide` overrides any edit.
 */
function finishComparison(
  local: string,
  merged: string,
  conflictEditIds: string[],
  decide: (editId: string) => 'accepted' | 'rejected' | undefined = () => undefined,
) {
  const { diffs, pendingEdits } = buildParagraphReviewData(local, merged);
  const edits = pendingEdits.map((edit) => {
    const status = decide(edit.id) ?? (conflictEditIds.includes(edit.id) ? 'rejected' as const : edit.status);
    return { ...edit, status };
  });
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
    const local = `${V1}作者补的一句。`;
    expect(finishComparison(local, result.merged, result.conflictEditIds)).toBe(`${V2}作者补的一句。`);
    // The only listed change is the AI's; rejecting it gives back the author's text, never less.
    expect(finishComparison(local, result.merged, result.conflictEditIds, () => 'rejected')).toBe(local);
  });

  it('merges edits at different places of the same paragraph', () => {
    const base = '老周还在摊上，他看了一眼手表，天快黑了。';
    const server = '第二天，老周还在摊上，他看了一眼手表，天快黑了。';
    const local = '老周还在摊上，他看了一眼手表，天快黑了。风很大。';
    const result = rebaseLocalEdits(base, local, server);
    expect(result.merged).toBe('第二天，老周还在摊上，他看了一眼手表，天快黑了。风很大。');
    expect(result.conflictEditIds).toEqual([]);
  });

  it('keeps the author text by default where both sides rewrote the same words; accepting takes the AI side', () => {
    const base = '第一段。\n\n老周看了一眼手表。\n\n第三段。';
    const server = '第一段。\n\n老周盯着那块旧表看了很久。\n\n第三段。';
    const local = '第一段。\n\n老周没有看表。\n\n第三段。作者加的。';
    const result = rebaseLocalEdits(base, local, server);
    expect(result.merged).toBe('第一段。\n\n老周盯着那块旧表看了很久。\n\n第三段。作者加的。');
    expect(result.proposal).toBe('第一段。\n\n老周没有看表。\n\n第三段。作者加的。');
    expect(result.conflictEditIds).toHaveLength(1);
    const conflictId = result.conflictEditIds[0];
    // The comparison lists the AI's rewrite against the author's sentence.
    const { pendingEdits } = buildParagraphReviewData(local, result.merged);
    const conflictEdit = pendingEdits.find((edit) => edit.id === conflictId);
    expect(conflictEdit?.oldText).toContain('老周没有看表');
    expect(conflictEdit?.newText).toContain('老周盯着那块旧表看了很久');
    // Finishing without choosing keeps the author's sentence and addition.
    expect(finishComparison(local, result.merged, result.conflictEditIds)).toBe(result.proposal);
    // Accepting the AI's rewrite there takes the AI side; the author's addition stays.
    expect(
      finishComparison(local, result.merged, result.conflictEditIds, (id) => (id === conflictId ? 'accepted' : undefined)),
    ).toBe(result.merged);
    // Rejecting every AI change gives back exactly the author's text.
    expect(finishComparison(local, result.merged, result.conflictEditIds, () => 'rejected')).toBe(local);
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

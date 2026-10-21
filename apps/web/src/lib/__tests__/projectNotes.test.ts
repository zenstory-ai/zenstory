import { describe, expect, it } from 'vitest';
import { adoptAllProjectProposals, displayProjectNotes, storedProjectNotes } from '../projectNotes';

describe('human adoption of existing project-note source sections', () => {
  it('keeps existing author entries and complete semicolon groups in one confirmed section', () => {
    expect(adoptAllProjectProposals('旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n第一组；保留补充\n第二组；保留结尾'))
      .toBe('旧约定\n\n【作者确认】\n原确认\n\n第一组；保留补充\n第二组；保留结尾');
  });

  it('recognizes the server-rendered header variants without adopting their explanations', () => {
    expect(adoptAllProjectProposals('【备注·未标来源】（旧内容）\r\n旧约定\r\n【备注·作者已确认】（作者保存）\r\n原确认\r\n【备注·ai 提议·待作者确认】（不是设定）\r\n待采纳'))
      .toBe('【备注·未标来源】（旧内容）\r\n旧约定\r\n【备注·作者已确认】（作者保存）\r\n原确认\r\n待采纳');
  });

  it('preserves author indentation, internal blank lines and trailing spaces while moving proposal bodies', () => {
    const author = '  旧约定  \n\n【作者确认】\n\t原确认  \n\n  第二段\n\n';
    expect(adoptAllProjectProposals(author + '【AI 提议·待作者确认】\n  新提议  \n\n补充'))
      .toBe(author + '  新提议  \n\n补充');
  });

  it.each(['', '作者正文提到了 AI 提议，但没有分区', '【作者确认】\n已有约定', '【AI 提议·待作者确认】\n\n【作者确认】\n已有约定'])('offers no adoption for absent or empty proposals: %s', notes => {
    expect(adoptAllProjectProposals(notes)).toBeNull();
  });
});

describe('note section headers in the interface language', () => {
  const stored = 'old rule\r\n【作者确认】\r\nNo swearing\r\n【AI 提议·待作者确认】\r\nTwist: she wrote the letters';

  it('shows English labels to English readers and stores the original headers back', () => {
    const shown = displayProjectNotes(stored, 'en-US');
    expect(shown).not.toMatch(/[\u4e00-\u9fff]/);
    expect(storedProjectNotes(shown, 'en-US')).toBe(stored);
    // Indentation around a header survives the round trip.
    expect(storedProjectNotes(displayProjectNotes('  【作者确认】', 'en'), 'en')).toBe('  【作者确认】');
  });

  it('never turns plain text into a header', () => {
    expect(displayProjectNotes(stored, 'zh')).toBe(stored);
    expect(storedProjectNotes('作者确认 is mentioned inline', 'en')).toBe('作者确认 is mentioned inline');
    // A Chinese reader's text is stored as typed, labels included.
    expect(storedProjectNotes('【Confirmed by you】', 'zh')).toBe('【Confirmed by you】');
    // An AI-written "[Confirmed by you]" line is not the label, so it cannot become author-confirmed.
    expect(storedProjectNotes('[Confirmed by you]', 'en')).toBe('[Confirmed by you]');
    // Lines that happen to be object property names are just text.
    for (const word of ['valueOf', 'toString', '__proto__', 'hasOwnProperty']) {
      expect(displayProjectNotes(word, 'en')).toBe(word);
      expect(storedProjectNotes(word, 'en')).toBe(word);
    }
  });
});

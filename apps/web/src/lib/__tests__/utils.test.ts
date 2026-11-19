import { describe, expect, it } from 'vitest';

import { stripThinkTags } from '../utils';

describe('stripThinkTags', () => {
  it('removes internal author edit context while preserving adjacent prose', () => {
    expect(stripThinkTags('已完成改写。\n\n<author_edits>无</author_edits>\n\n可以继续写下一章。'))
      .toBe('已完成改写。\n\n\n\n可以继续写下一章。');
  });

  it('removes multiple internal author edit blocks without filtering other markup', () => {
    expect(stripThinkTags('开头<author_edits>第一处</author_edits><strong>保留</strong><author_edits>第二处</author_edits>结尾'))
      .toBe('开头<strong>保留</strong>结尾');
  });

  it('hides an unfinished internal author edit block while streaming', () => {
    expect(stripThinkTags('可见回复。<author_edits>正在生成的内部上下文'))
      .toBe('可见回复。');
    expect(stripThinkTags('可见回复。<author_ed'))
      .toBe('可见回复。');
  });

  it('does not eat prose after inline-code mentions of `<author_edits>`', () => {
    expect(stripThinkTags('系统内部块 `<author_edits>` 不会向作者展示，后续正文正常保留。'))
      .toBe('系统内部块 `<author_edits>` 不会向作者展示，后续正文正常保留。');
  });
});

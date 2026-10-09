import { describe, expect, it } from "vitest";
import { countCharacters, truncateNovelText } from "../novelChapterSplit";

// Same fixtures as apps/server/tests/test_services/test_novel_text.py: the
// browser must cut a book where the server's splitter would.
const BODY = "他推开门，院子里的雪已经积了半尺深，远处传来钟声。".repeat(6);

function chapterCount(text: string): number {
  return truncateNovelText(text, Number.MAX_SAFE_INTEGER).totalChapters;
}

describe("truncateNovelText", () => {
  it("cuts on the heading of the next chapter, keeping short chapters merged into the last kept one", () => {
    const text = [
      BODY, // becomes 序章
      "第一卷 风起",
      "第一章 开始",
      BODY,
      "第二章 请假条",
      "今天停更一天", // short: merged into 第一章
      "第三章 继续",
      BODY,
      "第二卷 云涌", // volume line right before the cut
      "第四章 远行",
      BODY,
      "第五章 归来",
      BODY,
    ].join("\r\n");

    const kept = truncateNovelText(text, 3);

    expect(kept).toMatchObject({ totalChapters: 5, keptChapters: 3, truncated: true });
    expect(kept.text).toContain("第三章 继续");
    expect(kept.text).toContain("今天停更一天");
    expect(kept.text).not.toContain("第四章");
    expect(chapterCount(kept.text)).toBe(3);
  });

  it("leaves books within the limit untouched", () => {
    const text = `第一章 开始\n${BODY}\n第二章 继续\n${BODY}`;

    expect(truncateNovelText(text, 20)).toEqual({
      text,
      totalChapters: 2,
      keptChapters: 2,
      truncated: false,
    });
    expect(truncateNovelText(BODY, 20).totalChapters).toBe(0);
  });

  it.each([
    ["第十回 风雪山神庙"],
    ["第二卷风起 第十二章 归来"],
    ["【第五章】标题"],
    ["Chapter 12: The Gate"],
    ["Chapter One"],
    ["12. 新的开始"],
    ["楔子"],
  ])("recognises the heading %s", (heading) => {
    expect(chapterCount(`${heading}\n${BODY}`)).toBe(1);
  });

  it.each([
    ["3.5亿年前，这片大陆还是一片汪洋"],
    ["2023.10.01 晴"],
    ["第一回合他就输了"],
    ["前言不搭后语"],
    ["第三章的内容他早就背熟了，可是考官偏偏问到了最后一节。"],
  ])("treats %s as body text", (line) => {
    expect(chapterCount(`第一章 开始\n${BODY}\n${line}\n${BODY}`)).toBe(1);
  });
});

describe("countCharacters", () => {
  it("counts code points like the server", () => {
    expect(countCharacters("字𠀀a")).toBe(3);
  });
});

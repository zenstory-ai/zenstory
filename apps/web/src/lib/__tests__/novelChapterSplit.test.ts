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
    ["《第一章》标题"],
    ["「第一章」标题"],
    ["『第一章』标题"],
    ["[第一章] 标题"],
    ["§§§第一章 标题"],
    ["§§§第一章 标题§§§"],
    ["### 第一章 标题"],
    ["### Chapter 1: The Gate ###"],
    ["☆、第一章 标题"],
    ["◆ 第一章 标题"],
    ["*** 第一章 标题 ***"],
    ["**第一章 标题**"],
    ["__第一章 标题__"],
    ["正文 第一章 标题"],
    ["正文：第12章 归来"],
    ["《第一章 标题》"],
    ["§§§ 正文 第一章 标题 ***"],
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
    ["“第一章很好看”"],
    ["‘第一章很好看’"],
    ['"Chapter 1 was great"'],
    ["'Chapter 1'"],
    ["【第一章》错配括号"],
    ["【第一章 未闭合"],
    ["*** 第一章的内容他早就背熟了。 ***"],
    ["§§§ " + "第1章 " + "长".repeat(40)],
    ["=".repeat(30) + " 第一章 " + "=".repeat(30)],
  ])("treats %s as body text", (line) => {
    expect(chapterCount(`第一章 开始\n${BODY}\n${line}\n${BODY}`)).toBe(1);
  });

  it("cuts decorative-prefixed chapters and decorative volume separators at the exact boundary", () => {
    const text = [
      "*** 第一卷 风起 ***",
      "§§§第一章 开始",
      BODY,
      "§§§第二章 继续",
      BODY,
      "### 第二卷 云涌 ###",
      "§§§第三章 远行",
      BODY,
      "§§§第四章 归来",
      BODY,
    ].join("\n");

    const kept = truncateNovelText(text, 2);

    expect(kept).toMatchObject({ totalChapters: 4, keptChapters: 2, truncated: true });
    expect(kept.text).toContain("§§§第二章 继续");
    expect(kept.text).not.toContain("§§§第三章 远行");
    expect(chapterCount(kept.text)).toBe(2);
  });

  it("does not sub-split oversized chapters during free-trial truncation", () => {
    const hugeBody = "风雪夜归人，灯火映长街。".repeat(2_000); // 24,000 chars (> 20,000)
    const text = `§§§第一章 巨章\n${hugeBody}\n§§§第二章 后续\n${BODY}`;

    const kept = truncateNovelText(text, 1);

    expect(kept).toMatchObject({ totalChapters: 2, keptChapters: 1, truncated: true });
    expect(countCharacters(kept.text)).toBeGreaterThan(20_000);
    expect(kept.text).not.toContain("§§§第二章 后续");
  });
});

// Same cases as test_heading_whitespace_parity_cases in
// apps/server/tests/test_services/test_novel_text.py: Python's str.strip and
// "." differ from JavaScript's trim and ".".
describe("Python whitespace parity", () => {
  it.each([
    ["﻿第二章 继续", 1],
    ["﻿§§§第二章 继续", 1],
    ["第二章 标 题", 2],
    ["2. 标 题", 2],
    ["\x1c第二章 继续\x1f", 2],
    ["\x1c§§§第二章 继续\x1f", 2],
    ["\x85第二章 继续", 2],
    ["第二章\x85继续", 2],
  ])("splits %j like the server", (middle, expected) => {
    expect(chapterCount(`第一章 开始\n${BODY}\n${middle}\n${BODY}`)).toBe(expected);
  });

  it("does not count stripped control characters toward chapter length", () => {
    const text = `第一章 开始\n${BODY}\n第二章 继续\n${"字".repeat(99)}\x1c\n第三章 末\n${BODY}`;

    expect(chapterCount(text)).toBe(2);
  });
});

describe("countCharacters", () => {
  it("counts code points like the server", () => {
    expect(countCharacters("字𠀀a")).toBe(3);
  });
});

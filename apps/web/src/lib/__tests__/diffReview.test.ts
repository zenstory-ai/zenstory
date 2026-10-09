import { describe, expect, it } from "vitest";

import {
  applyPendingEditsToDiffs,
  buildPendingEditsFromDiffs,
  buildReviewSegmentsFromDiffs,
  computeParagraphReviewDiffs,
} from "../diffReview";

describe("diffReview paragraph helpers", () => {
  it("turns a paragraph rewrite into a single replace edit", () => {
    const original = [
      "第一段保持不变。",
      "",
      "第二段原文。",
      "",
      "第三段保持不变。",
    ].join("\n");
    const modified = [
      "第一段保持不变。",
      "",
      "第二段改写后内容。",
      "",
      "第三段保持不变。",
    ].join("\n");

    const diffs = computeParagraphReviewDiffs(original, modified);
    const edits = buildPendingEditsFromDiffs(diffs);

    expect(edits).toHaveLength(1);
    expect(edits[0]).toMatchObject({
      id: "edit-0",
      op: "replace",
      oldText: "第二段原文。\n\n",
      newText: "第二段改写后内容。\n\n",
      status: "pending",
    });
    expect(applyPendingEditsToDiffs(diffs, edits)).toBe(modified);
  });

  it("keeps the original content when an inserted paragraph is rejected", () => {
    const original = ["开头段落。", "", "结尾段落。"].join("\n");
    const modified = ["开头段落。", "", "新增段落。", "", "结尾段落。"].join("\n");

    const diffs = computeParagraphReviewDiffs(original, modified);
    const edits = buildPendingEditsFromDiffs(diffs);

    expect(edits).toHaveLength(1);
    expect(edits[0]).toMatchObject({
      id: "edit-0",
      op: "insert_after",
      oldText: "",
      newText: "新增段落。\n\n",
      status: "pending",
    });

    const rejectedEdits = edits.map((edit) => ({ ...edit, status: "rejected" as const }));

    expect(applyPendingEditsToDiffs(diffs, rejectedEdits)).toBe(original);
  });

  it("preserves stable edit indexes for replace + insert sequences", () => {
    const original = ["保留段落。", "", "旧段落。"].join("\n");
    const modified = ["保留段落。", "", "新段落。", "", "补充段落。"].join("\n");

    const segments = buildReviewSegmentsFromDiffs(
      computeParagraphReviewDiffs(original, modified)
    );

    expect(segments).toEqual([
      { type: "equal", text: "保留段落。\n\n" },
      {
        type: "replace",
        text: "旧段落。",
        newText: "新段落。\n\n",
        editIndex: 0,
      },
      {
        type: "insert",
        text: "补充段落。",
        editIndex: 1,
      },
    ]);
  });

  it("leaves U+3000-indented paragraphs byte-identical when every edit is rejected", () => {
    const original = [
      "　　市三院的走廊，永远比外头冷。",
      "",
      "　　江远没看电子屏。",
      "",
      "　　他不敢看。",
      "",
    ].join("\n");
    // 改写吃掉了全部段首缩进，只真正改了第二段，结尾也少了换行。
    const modified = [
      "市三院的走廊，永远比外头冷。",
      "",
      "江远没去看电子屏。",
      "",
      "他不敢看。",
    ].join("\n");

    const diffs = computeParagraphReviewDiffs(original, modified);
    const edits = buildPendingEditsFromDiffs(diffs);
    const rejected = edits.map((edit) => ({ ...edit, status: "rejected" as const }));

    expect(edits).toHaveLength(1);
    expect(applyPendingEditsToDiffs(diffs, rejected)).toBe(original);
  });

  it("keeps the original indentation for paragraphs that differ only in whitespace", () => {
    const original = ["　　第一段。", "", "　　第二段原文。"].join("\n");
    const modified = ["第一段。", "", "　　第二段改写。"].join("\n");

    const diffs = computeParagraphReviewDiffs(original, modified);
    const edits = buildPendingEditsFromDiffs(diffs);

    expect(edits).toHaveLength(1);
    expect(applyPendingEditsToDiffs(diffs, edits)).toBe(
      ["　　第一段。", "", "　　第二段改写。"].join("\n")
    );
  });

  it("does not glue an accepted new paragraph onto an unchanged last paragraph", () => {
    const original = ["　　开头。", "", "　　结尾。"].join("\n");
    const modified = ["　　开头。", "", "结尾。", "", "　　补一段。"].join("\n");

    const diffs = computeParagraphReviewDiffs(original, modified);
    const edits = buildPendingEditsFromDiffs(diffs);
    const rejected = edits.map((edit) => ({ ...edit, status: "rejected" as const }));

    expect(edits).toHaveLength(1);
    expect(applyPendingEditsToDiffs(diffs, edits)).toBe(
      ["　　开头。", "", "　　结尾。", "", "　　补一段。"].join("\n")
    );
    expect(applyPendingEditsToDiffs(diffs, rejected)).toBe(original);
  });

  it("filters unchanged replacements created by whitespace-only differences", () => {
    const original = ["“这是我们的合作方案。”", "", "第二段原文。"].join("\n");
    const modified = ["“这是我们的合作方案。”   ", "", "第二段改写。"].join("\n");

    const diffs = computeParagraphReviewDiffs(original, modified);
    const edits = buildPendingEditsFromDiffs(diffs);

    expect(edits).toHaveLength(1);
    expect(edits[0]).toMatchObject({
      id: "edit-0",
      op: "replace",
      oldText: "第二段原文。",
      newText: "第二段改写。",
      status: "pending",
    });
  });
});

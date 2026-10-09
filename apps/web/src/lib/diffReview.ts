import {
  DIFF_DELETE,
  DIFF_EQUAL,
  DIFF_INSERT,
  diff_match_patch,
} from "diff-match-patch";
import type { PendingEdit } from "../types";

export type ReviewDiffTuple = [number, string];

// A lone \r only counts when no \n follows, so "\r\n" has exactly one parse (no catastrophic backtracking).
const PARAGRAPH_BREAK_RE = /(?:\r\n|\n|\r(?!\n))(?:[ \t]*(?:\r\n|\n|\r(?!\n)))+/g;

function normalizeComparableText(text: string): string {
  return text.replace(/\r\n/g, "\n").trim();
}

function isVisuallyEmptyText(text: string): boolean {
  return normalizeComparableText(text).length === 0;
}

function isNoopReplacement(oldText: string, newText: string): boolean {
  return normalizeComparableText(oldText) === normalizeComparableText(newText);
}


const isLineBreakChar = (char: string | undefined): boolean => char === "\n" || char === "\r";

/**
 * The trailing paragraph break (two or more line breaks, with only spaces/tabs
 * between them) at the very end of `text`, or "". A backward linear scan: an
 * end-anchored regex here costs quadratic time on long runs of line breaks.
 */
function trailingParagraphBreak(text: string): string {
  if (!isLineBreakChar(text[text.length - 1])) return "";
  let index = text.length;
  let start = index;
  let breaks = 0;
  while (index > 0) {
    const char = text[index - 1];
    if (char === "\n") {
      index -= index >= 2 && text[index - 2] === "\r" ? 2 : 1;
      breaks += 1;
      start = index;
      continue;
    }
    if (char === "\r") {
      index -= 1;
      breaks += 1;
      start = index;
      continue;
    }
    if (char === " " || char === "\t") {
      let spaceStart = index;
      while (spaceStart > 0 && (text[spaceStart - 1] === " " || text[spaceStart - 1] === "\t")) {
        spaceStart -= 1;
      }
      // Spaces only belong to the break when another line break precedes them.
      if (!isLineBreakChar(text[spaceStart - 1])) break;
      index = spaceStart;
      continue;
    }
    break;
  }
  return breaks >= 2 ? text.slice(start) : "";
}

function endsWithParagraphBreak(text: string): boolean {
  return trailingParagraphBreak(text) !== "";
}

export function splitParagraphBlocks(text: string): string[] {
  if (!text) return [];

  const blocks: string[] = [];
  let lastIndex = 0;

  for (const match of text.matchAll(PARAGRAPH_BREAK_RE)) {
    const separatorStart = match.index ?? 0;
    const separator = match[0] ?? "";
    const block = text.slice(lastIndex, separatorStart + separator.length);
    if (block) {
      blocks.push(block);
    }
    lastIndex = separatorStart + separator.length;
  }

  if (lastIndex < text.length) {
    blocks.push(text.slice(lastIndex));
  }

  return blocks.length > 0 ? blocks : [text];
}

function encodeParagraphTokens(
  originalContent: string,
  modifiedContent: string
): { encodedOriginal: string; encodedModified: string; tokenTable: string[] } {
  const tokenTable = [""];
  const tokenToCodePoint = new Map<string, number>();

  const encode = (tokens: string[]) =>
    tokens
      .map((token) => {
        const existing = tokenToCodePoint.get(token);
        if (existing != null) {
          return String.fromCodePoint(existing);
        }
        const nextCodePoint = tokenTable.length;
        tokenTable.push(token);
        tokenToCodePoint.set(token, nextCodePoint);
        return String.fromCodePoint(nextCodePoint);
      })
      .join("");

  return {
    encodedOriginal: encode(splitParagraphBlocks(originalContent)),
    encodedModified: encode(splitParagraphBlocks(modifiedContent)),
    tokenTable,
  };
}

function decodeTokenString(encoded: string, tokenTable: string[]): string {
  if (!encoded) return "";

  let decoded = "";
  for (const char of encoded) {
    const codePoint = char.codePointAt(0) ?? 0;
    const token = tokenTable[codePoint];

    // Some tests mock diff-match-patch with already-decoded text segments rather
    // than the encoded token stream produced above. Keep the fallback scoped to
    // test mode so production failures do not get silently masked.
    if (token == null) {
      if (import.meta.env.MODE === "test") {
        return encoded;
      }

      throw new Error(
        `[diffReview] Unknown paragraph token while decoding diff output: ${codePoint}`
      );
    }

    decoded += token;
  }
  return decoded;
}

export function computeParagraphReviewDiffs(
  originalContent: string,
  modifiedContent: string
): ReviewDiffTuple[] {
  const dmp = new diff_match_patch();
  const { encodedOriginal, encodedModified, tokenTable } = encodeParagraphTokens(
    originalContent,
    modifiedContent
  );

  const encodedDiffs = dmp.diff_main(encodedOriginal, encodedModified, false);
  dmp.diff_cleanupSemantic(encodedDiffs);

  return encodedDiffs.map(([op, encodedText]) => [
    op,
    decodeTokenString(encodedText, tokenTable),
  ]);
}

export function buildPendingEditsFromDiffs(diffs: ReviewDiffTuple[]): PendingEdit[] {
  return buildReviewSegmentsFromDiffs(diffs).flatMap((segment) => {
    if (segment.type === "equal") {
      return [];
    }

    return [
      {
        id: `edit-${segment.editIndex ?? 0}`,
        op:
          segment.type === "replace"
            ? "replace"
            : segment.type === "delete"
              ? "delete"
              : "insert_after",
        oldText: segment.type === "insert" ? "" : segment.text,
        newText: segment.type === "replace" ? segment.newText ?? "" : segment.type === "insert" ? segment.text : "",
        status: "pending" as const,
      },
    ];
  });
}

export interface ReviewDiffSegment {
  type: "equal" | "delete" | "insert" | "replace";
  text: string;
  newText?: string;
  editIndex?: number;
}

function buildAtomicReviewSegments(diffs: ReviewDiffTuple[]): ReviewDiffSegment[] {
  const segments: ReviewDiffSegment[] = [];
  let editIndex = 0;

  for (let i = 0; i < diffs.length; i++) {
    const [operation, text] = diffs[i];

    if (operation === DIFF_EQUAL) {
      segments.push({ type: "equal", text });
      continue;
    }

    if (operation === DIFF_DELETE) {
      if (i + 1 < diffs.length && diffs[i + 1][0] === DIFF_INSERT) {
        const oldBlocks = splitParagraphBlocks(text);
        const newBlocks = splitParagraphBlocks(diffs[i + 1][1]);
        const pairCount = Math.min(oldBlocks.length, newBlocks.length);
        // 原文最后一段没有段落分隔符、而改写在它后面又补了段落时，分隔符挂到
        // 下一个插入段前面：拒绝插入时原文逐字节不变，接受时两段也不会粘在一起。
        let separatorForNextInsert = "";

        for (let blockIndex = 0; blockIndex < pairCount; blockIndex++) {
          const oldBlock = oldBlocks[blockIndex]!;
          const newBlock = newBlocks[blockIndex]!;

          if (isNoopReplacement(oldBlock, newBlock)) {
            // 只差空白的段落按原文保留：U+3000 段首缩进、行尾空白、段落分隔都不动。
            // 用改写后的文本会让「全部拒绝」也悄悄改掉原文格式。
            segments.push({
              type: "equal",
              text: oldBlock,
            });
            separatorForNextInsert = endsWithParagraphBreak(oldBlock)
              ? ""
              : trailingParagraphBreak(newBlock);
            continue;
          }
          separatorForNextInsert = "";

          segments.push({
            type: "replace",
            text: oldBlock,
            newText: newBlock,
            editIndex: editIndex++,
          });
        }

        for (let blockIndex = pairCount; blockIndex < oldBlocks.length; blockIndex++) {
          const block = oldBlocks[blockIndex]!;
          if (isVisuallyEmptyText(block)) {
            // 纯空白块不值得审阅，但它是原文的一部分，保留下来。
            segments.push({ type: "equal", text: block });
            continue;
          }

          segments.push({
            type: "delete",
            text: block,
            editIndex: editIndex++,
          });
        }

        for (let blockIndex = pairCount; blockIndex < newBlocks.length; blockIndex++) {
          const block = newBlocks[blockIndex]!;
          if (isVisuallyEmptyText(block)) {
            // 改写多出来的纯空白块不是原文，也不值得审阅，直接丢掉。
            continue;
          }

          segments.push({
            type: "insert",
            text: separatorForNextInsert + block,
            editIndex: editIndex++,
          });
          separatorForNextInsert = "";
        }

        i++;
      } else {
        for (const block of splitParagraphBlocks(text)) {
          if (isVisuallyEmptyText(block)) {
            segments.push({ type: "equal", text: block });
            continue;
          }

          segments.push({
            type: "delete",
            text: block,
            editIndex: editIndex++,
          });
        }
      }
      continue;
    }

    if (operation === DIFF_INSERT) {
      for (const block of splitParagraphBlocks(text)) {
        if (isVisuallyEmptyText(block)) {
          continue;
        }

        segments.push({
          type: "insert",
          text: block,
          editIndex: editIndex++,
        });
      }
    }
  }

  return segments;
}

export function buildReviewSegmentsFromDiffs(
  diffs: ReviewDiffTuple[]
): ReviewDiffSegment[] {
  return buildAtomicReviewSegments(diffs);
}

export function applyPendingEditsToDiffs(
  diffs: ReviewDiffTuple[],
  pendingEdits: PendingEdit[]
): string {
  const segments = buildAtomicReviewSegments(diffs);
  let result = "";

  for (const segment of segments) {
    if (segment.type === "equal") {
      result += segment.text;
      continue;
    }

    const edit = segment.editIndex != null ? pendingEdits[segment.editIndex] : undefined;
    const isAcceptedByDefault = !edit || edit.status !== "rejected";

    if (segment.type === "replace") {
      result += isAcceptedByDefault ? segment.newText ?? "" : segment.text;
    } else if (segment.type === "delete") {
      if (!isAcceptedByDefault) {
        result += segment.text;
      }
    } else if (isAcceptedByDefault) {
      result += segment.text;
    }
  }

  return result;
}

export function buildParagraphReviewData(
  originalContent: string,
  modifiedContent: string
): { diffs: ReviewDiffTuple[]; pendingEdits: PendingEdit[] } {
  const diffs = computeParagraphReviewDiffs(originalContent, modifiedContent);
  return {
    diffs,
    pendingEdits: buildPendingEditsFromDiffs(diffs),
  };
}

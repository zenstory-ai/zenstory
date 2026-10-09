/**
 * Three-way merge for "the author kept typing on an older copy while someone
 * else (the AI) saved a newer one".
 *
 * base   = the copy the author started from (what the editor last loaded/saved)
 * local  = the author's unsaved text (base + the author's edits)
 * server = the newer saved copy (base + the AI's edits)
 *
 * The author's edits are replayed on top of `server`, first per paragraph and,
 * where both sides touched the same paragraphs, per character. Regions both
 * sides changed in incompatible ways are conflicts: the merged text keeps the
 * server side there, the proposal shows the author's side.
 */
import {
  DIFF_DELETE,
  DIFF_EQUAL,
  diff_match_patch,
} from "diff-match-patch";
import {
  buildReviewSegmentsFromDiffs,
  computeParagraphReviewDiffs,
  splitParagraphBlocks,
} from "./diffReview";

type Diff = [number, string];

interface Hunk {
  /** Replaced token range [start, end) in the base sequence. */
  start: number;
  end: number;
  tokens: string[];
  side: "local" | "server";
}

interface MergePiece {
  /** Text with conflicts resolved to the server side. */
  merged: string;
  /** Text with conflicts resolved to the author's side. */
  proposal: string;
  /** The server copy of the same region. */
  server: string;
  conflict: boolean;
}

export interface RebaseResult {
  /** server + every author edit that applies cleanly; conflicts keep the server text. */
  merged: string;
  /** server + every author edit, conflicts included (the comparison's right side). */
  proposal: string;
  /**
   * Review edit ids (as produced by `buildParagraphReviewData(server, proposal)`)
   * that touch a conflict. They should start rejected so finishing the review
   * without looking keeps the server side there.
   */
  conflictEditIds: string[];
}

/** Paragraph tokens are encoded as single UTF-16 code units below the surrogate range. */
const MAX_ENCODED_TOKENS = 0xd7ff;

function hunksFromDiffs(diffs: Diff[], decode: (unit: string) => string, side: Hunk["side"]): Hunk[] {
  const hunks: Hunk[] = [];
  let pos = 0;
  let current: Hunk | null = null;
  for (const [op, text] of diffs) {
    if (op === DIFF_EQUAL) {
      if (current) hunks.push(current);
      current = null;
      pos += text.length;
      continue;
    }
    if (!current) current = { start: pos, end: pos, tokens: [], side };
    if (op === DIFF_DELETE) {
      pos += text.length;
      current.end = pos;
    } else {
      for (const unit of text) current.tokens.push(decode(unit));
    }
  }
  if (current) hunks.push(current);
  return hunks;
}

/** One side's text for base[start, end) with that side's hunks applied. */
function sideText(base: string[], hunks: Hunk[], start: number, end: number): string {
  let out = "";
  let pos = start;
  for (const hunk of hunks) {
    out += base.slice(pos, hunk.start).join("");
    out += hunk.tokens.join("");
    pos = hunk.end;
  }
  return out + base.slice(pos, end).join("");
}

function mergeHunks(
  base: string[],
  localHunks: Hunk[],
  serverHunks: Hunk[],
  resolveBothChanged: (baseText: string, localText: string, serverText: string) => MergePiece[],
): MergePiece[] {
  const all = [...localHunks, ...serverHunks].sort((a, b) => a.start - b.start || a.end - b.end);
  const pieces: MergePiece[] = [];
  const pushEqual = (text: string) => {
    if (text) pieces.push({ merged: text, proposal: text, server: text, conflict: false });
  };
  let pos = 0;
  let i = 0;
  while (i < all.length) {
    const group = [all[i]!];
    const clusterStart = all[i]!.start;
    let clusterEnd = all[i]!.end;
    i += 1;
    // Overlapping ranges, or two changes at the same spot, form one region.
    while (i < all.length && (all[i]!.start < clusterEnd || all[i]!.start === clusterStart)) {
      group.push(all[i]!);
      clusterEnd = Math.max(clusterEnd, all[i]!.end);
      i += 1;
    }
    pushEqual(base.slice(pos, clusterStart).join(""));
    pos = clusterEnd;

    const local = group.filter((hunk) => hunk.side === "local");
    const server = group.filter((hunk) => hunk.side === "server");
    const baseText = base.slice(clusterStart, clusterEnd).join("");
    const serverText = sideText(base, server, clusterStart, clusterEnd);
    if (local.length === 0) {
      pieces.push({ merged: serverText, proposal: serverText, server: serverText, conflict: false });
      continue;
    }
    const localText = sideText(base, local, clusterStart, clusterEnd);
    if (server.length === 0 || localText === serverText) {
      pieces.push({ merged: localText, proposal: localText, server: serverText, conflict: false });
      continue;
    }
    pieces.push(...resolveBothChanged(baseText, localText, serverText));
  }
  pushEqual(base.slice(pos).join(""));
  return pieces;
}

function charLevelMerge(base: string, local: string, server: string): MergePiece[] {
  const dmp = new diff_match_patch();
  dmp.Diff_Timeout = 0.2;
  const diff = (other: string): Diff[] => {
    const diffs = dmp.diff_main(base, other, false) as Diff[];
    // Group scattered single-character matches into whole rewrites, so an edit
    // inside a sentence the other side rewrote is a conflict, not a splice.
    dmp.diff_cleanupSemantic(diffs);
    return diffs;
  };
  const identity = (unit: string) => unit;
  return mergeHunks(
    base.split(""),
    hunksFromDiffs(diff(local), identity, "local"),
    hunksFromDiffs(diff(server), identity, "server"),
    (_baseText, localText, serverText) => [
      { merged: serverText, proposal: localText, server: serverText, conflict: true },
    ],
  );
}

function paragraphLevelMerge(base: string, local: string, server: string): MergePiece[] {
  const table: string[] = [];
  const codeByToken = new Map<string, string>();
  let overflow = false;
  const encode = (text: string) =>
    splitParagraphBlocks(text)
      .map((token) => {
        const existing = codeByToken.get(token);
        if (existing) return existing;
        if (table.length + 1 >= MAX_ENCODED_TOKENS) {
          overflow = true;
          return "";
        }
        table.push(token);
        const unit = String.fromCharCode(table.length);
        codeByToken.set(token, unit);
        return unit;
      })
      .join("");
  const encodedBase = encode(base);
  const encodedLocal = encode(local);
  const encodedServer = encode(server);
  if (overflow) return charLevelMerge(base, local, server);

  const dmp = new diff_match_patch();
  dmp.Diff_Timeout = 0.5;
  const decode = (unit: string) => table[unit.charCodeAt(0) - 1] ?? "";
  const baseTokens = Array.from(encodedBase, decode);
  return mergeHunks(
    baseTokens,
    hunksFromDiffs(dmp.diff_main(encodedBase, encodedLocal, false) as Diff[], decode, "local"),
    hunksFromDiffs(dmp.diff_main(encodedBase, encodedServer, false) as Diff[], decode, "server"),
    charLevelMerge,
  );
}

function rangesTouch(a0: number, a1: number, b0: number, b1: number): boolean {
  if (a0 === a1) return b0 <= a0 && a0 <= b1;
  if (b0 === b1) return a0 <= b0 && b0 <= a1;
  return a0 < b1 && b0 < a1;
}

function findConflictEditIds(server: string, proposal: string, conflicts: Array<[number, number]>): string[] {
  if (conflicts.length === 0) return [];
  const segments = buildReviewSegmentsFromDiffs(computeParagraphReviewDiffs(server, proposal));
  const ids: string[] = [];
  let pos = 0;
  for (const segment of segments) {
    const start = pos;
    const end = segment.type === "insert" ? pos : pos + segment.text.length;
    pos = end;
    if (segment.type === "equal" || segment.editIndex == null) continue;
    if (conflicts.some(([c0, c1]) => rangesTouch(start, end, c0, c1))) {
      ids.push(`edit-${segment.editIndex}`);
    }
  }
  return ids;
}

/** Replays the author's edits (base → local) on top of the newer server copy. */
export function rebaseLocalEdits(base: string, local: string, server: string): RebaseResult {
  if (local === base || local === server) return { merged: server, proposal: server, conflictEditIds: [] };
  if (server === base) return { merged: local, proposal: local, conflictEditIds: [] };

  const pieces = paragraphLevelMerge(base, local, server);
  let merged = "";
  let proposal = "";
  let serverPos = 0;
  const conflicts: Array<[number, number]> = [];
  for (const piece of pieces) {
    merged += piece.merged;
    proposal += piece.proposal;
    if (piece.conflict) conflicts.push([serverPos, serverPos + piece.server.length]);
    serverPos += piece.server.length;
  }
  return { merged, proposal, conflictEditIds: findConflictEditIds(server, proposal, conflicts) };
}

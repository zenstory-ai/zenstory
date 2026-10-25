/**
 * Project notes keep their source sections under fixed Chinese header lines (server
 * `config/project_notes.py`). The panel shows and edits only the author's entries without headers.
 * Notes saved before the AI-proposal removal may still hold a legacy 【AI 提议…】 block: the server
 * leaves it out of every read, and it is neither shown nor written back here either.
 */
const CONFIRMED_HEADER = '【作者确认】';
const UNLABELED_HEADER = '【未标来源】';

const HEADER_PREFIX = '^\\s*【\\s*(?:备注\\s*[·・]\\s*)?';
const HEADER_SUFFIX = '\\s*】\\s*(?:[（(].*[)）])?\\s*$';
const confirmedHeader = new RegExp(HEADER_PREFIX + '作者(?:已)?确认' + HEADER_SUFFIX);
const legacyProposalsHeader = new RegExp(HEADER_PREFIX + '(?:作者已忽略的\\s*)?AI\\s*提议[^】]*' + HEADER_SUFFIX, 'i');
const unlabeledHeader = new RegExp(HEADER_PREFIX + '未标来源' + HEADER_SUFFIX);

const isHeaderLine = (line: string) =>
  confirmedHeader.test(line) || legacyProposalsHeader.test(line) || unlabeledHeader.test(line);

// An author line shaped like a section header is stored behind a zero-width space (which neither
// the server nor these header patterns accept as leading whitespace) so it stays author text.
// Already-escaped lines get one more, keeping the mapping reversible.
const ESCAPE = '\u200b';
const needsEscape = (line: string): boolean =>
  isHeaderLine(line) || (line.startsWith(ESCAPE) && needsEscape(line.slice(ESCAPE.length)));
const escapeAuthorLine = (line: string) => (needsEscape(line) ? ESCAPE + line : line);
const unescapeAuthorLine = (line: string) =>
  line.startsWith(ESCAPE) && needsEscape(line.slice(ESCAPE.length)) ? line.slice(ESCAPE.length) : line;

type BlockKind = 'unlabeled' | 'confirmed' | 'legacy';
interface Block { kind: BlockKind; lines: string[] }

function parseBlocks(notes: string): Block[] {
  const blocks: Block[] = [{ kind: 'unlabeled', lines: [] }];
  let current = blocks[0];
  for (const line of notes.split(/\r?\n/)) {
    const kind: BlockKind | null = confirmedHeader.test(line) ? 'confirmed'
      : legacyProposalsHeader.test(line) ? 'legacy'
        : unlabeledHeader.test(line) ? 'unlabeled' : null;
    if (kind) {
      current = { kind, lines: [] };
      blocks.push(current);
    } else current.lines.push(line);
  }
  return blocks;
}

const lineEnding = (notes: string) => (notes.includes('\r\n') ? '\r\n' : '\n');

/**
 * The author's own notes (unlabeled and confirmed entries) without section headers, in stored
 * order. The blank line before a legacy proposal block is not shown.
 */
export function authorNotesText(notes: string | null | undefined): string {
  const lines: string[] = [];
  for (const block of parseBlocks(notes ?? '')) {
    if (block.kind === 'legacy') {
      if (lines.length > 0 && !lines[lines.length - 1].trim()) lines.pop();
      continue;
    }
    lines.push(...block.lines.map(unescapeAuthorLine));
  }
  return lines.join('\n');
}

const confirmedLines = (notes: string) => new Set(parseBlocks(notes)
  .filter(block => block.kind === 'confirmed')
  .flatMap(block => block.lines.map(line => line.trim()).filter(Boolean)));

/**
 * Stored notes after the author edited `authorNotesText(stored)` into `edited`. A line exactly
 * equal (trimmed) to a confirmed line of `stored` (or of `confirmedFrom`, e.g. the saved baseline)
 * stays confirmed; every other line is unlabeled. Order is kept by switching headers where the
 * source changes; a legacy proposal block is dropped. A typed line shaped like a section header
 * stays author text (see `escapeAuthorLine`).
 * `authorNotesText(withAuthorNotes(s, e)) === e` and `withAuthorNotes(s, authorNotesText(s)) === s`;
 * typing back the author text of `confirmedFrom` returns it unchanged, so a restored draft is not
 * dirty even when the saved layout is not the canonical one.
 */
export function withAuthorNotes(stored: string, edited: string, confirmedFrom?: string): string {
  if (edited === authorNotesText(stored)) return stored;
  if (confirmedFrom !== undefined && edited === authorNotesText(confirmedFrom)) return confirmedFrom;
  const confirmed = confirmedLines(stored);
  if (confirmedFrom) confirmedLines(confirmedFrom).forEach(line => confirmed.add(line));
  const out: string[] = [];
  if (edited !== '') {
    let current: BlockKind = 'unlabeled';
    for (const typed of edited.split(/\r?\n/)) {
      const line = escapeAuthorLine(typed);
      if (line.trim()) {
        const kind: BlockKind = confirmed.has(line.trim()) ? 'confirmed' : 'unlabeled';
        if (kind !== current) {
          out.push(kind === 'confirmed' ? CONFIRMED_HEADER : UNLABELED_HEADER);
          current = kind;
        }
      }
      out.push(line);
    }
  }
  return out.join(lineEnding(stored));
}

export interface NoteEntriesRemoval {
  /** Stored notes without the removed lines (and without an 【未标来源】 header they left empty). */
  text: string;
  /** Entries found and removed, by exact trimmed equality with a whole stored line. */
  removed: string[];
  /** Entries no longer stored as written (the author edited or deleted them). */
  missing: string[];
}

/**
 * Undo of the entries one chat update_project appended (`notes_added`, each stored on its own
 * line): removes the last line exactly equal (trimmed) to each entry, never a header or a line of
 * a legacy proposal block, and keeps every other byte. An entry the author has since edited stays.
 */
export function withoutNoteEntries(stored: string, entries: string[]): NoteEntriesRemoval {
  const lines = stored.split(/\r?\n/);
  const legacy = new Set<number>();
  let inLegacy = false;
  lines.forEach((line, index) => {
    if (isHeaderLine(line)) inLegacy = legacyProposalsHeader.test(line);
    if (inLegacy) legacy.add(index);
  });
  const drop = new Set<number>();
  const removed: string[] = [];
  const missing: string[] = [];
  for (const entry of entries) {
    const target = entry.trim();
    let found = -1;
    for (let index = lines.length - 1; index >= 0 && target; index--) {
      const line = lines[index];
      if (drop.has(index) || legacy.has(index) || isHeaderLine(line)) continue;
      if (line.trim() === target) { found = index; break; }
    }
    if (found >= 0) {
      drop.add(found);
      removed.push(entry);
    } else missing.push(entry);
  }
  if (drop.size === 0) return { text: stored, removed, missing };
  const kept = lines.filter((_, index) => !drop.has(index));
  // An 【未标来源】 header that only introduced the removed lines goes with them.
  const out = kept.filter((line, index) => {
    if (!unlabeledHeader.test(line)) return true;
    const rest = kept.slice(index + 1);
    const next = rest.findIndex(isHeaderLine);
    return (next < 0 ? rest : rest.slice(0, next)).some(other => other.trim());
  });
  return { text: out.join(lineEnding(stored)).trimEnd(), removed, missing };
}

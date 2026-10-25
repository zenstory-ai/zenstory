/**
 * Project notes keep their source sections under fixed Chinese header lines (server
 * `config/project_notes.py`). The author edits only their own entries; AI proposals are
 * adopted or dropped one entry at a time by explicit human actions. Nothing here infers intent:
 * entries are identified by exact (trimmed) equality with the stored text.
 */
const CONFIRMED_HEADER = '【作者确认】';
const UNLABELED_HEADER = '【未标来源】';

const HEADER_PREFIX = '^\\s*【\\s*(?:备注\\s*[·・]\\s*)?';
const HEADER_SUFFIX = '\\s*】\\s*(?:[（(].*[)）])?\\s*$';
const confirmedHeader = new RegExp(HEADER_PREFIX + '作者(?:已)?确认' + HEADER_SUFFIX);
const proposalsHeader = new RegExp(HEADER_PREFIX + 'AI\\s*提议[^】]*' + HEADER_SUFFIX, 'i');
const unlabeledHeader = new RegExp(HEADER_PREFIX + '未标来源' + HEADER_SUFFIX);

const isHeaderLine = (line: string) =>
  confirmedHeader.test(line) || proposalsHeader.test(line) || unlabeledHeader.test(line);

// An author line shaped like a section header is stored behind a zero-width space (which neither
// the server nor these header patterns accept as leading whitespace) so it stays author text.
// Already-escaped lines get one more, keeping the mapping reversible.
const ESCAPE = '\u200b';
const needsEscape = (line: string): boolean =>
  isHeaderLine(line) || (line.startsWith(ESCAPE) && needsEscape(line.slice(ESCAPE.length)));
const escapeAuthorLine = (line: string) => (needsEscape(line) ? ESCAPE + line : line);
const unescapeAuthorLine = (line: string) =>
  line.startsWith(ESCAPE) && needsEscape(line.slice(ESCAPE.length)) ? line.slice(ESCAPE.length) : line;

type BlockKind = 'unlabeled' | 'confirmed' | 'proposals';
interface Block { kind: BlockKind; header?: string; lines: string[] }

function parseBlocks(notes: string): Block[] {
  const blocks: Block[] = [{ kind: 'unlabeled', lines: [] }];
  let current = blocks[0];
  for (const line of notes.split(/\r?\n/)) {
    const kind: BlockKind | null = confirmedHeader.test(line) ? 'confirmed'
      : proposalsHeader.test(line) ? 'proposals'
        : unlabeledHeader.test(line) ? 'unlabeled' : null;
    if (kind) {
      current = { kind, header: line, lines: [] };
      blocks.push(current);
    } else current.lines.push(line);
  }
  return blocks;
}

const lineEnding = (notes: string) => (notes.includes('\r\n') ? '\r\n' : '\n');
const joinBlocks = (blocks: Block[], eol: string) => blocks
  .flatMap(block => [...(block.header !== undefined ? [block.header] : []), ...block.lines])
  .join(eol);

// Mirrors the server `_BULLET_RE`, `_SELF_LABEL_RE` and `_IGNORABLE_RE`: a part whose entry key is
// empty (only a bullet, a self label, whitespace or punctuation) is not an entry.
const BULLET = /^\s*(?:[-*•·]+|\p{Nd}+[.、)）]|[（(]\p{Nd}+[)）])\s*/u;
const SELF_LABEL = /[【[（(]\s*(?:作者)?(?:已)?(?:确认|定|拍板)(?:设定|的?事实)?\s*[】\]）)]/gu;
const IGNORABLE = /[\s\u3000，,。.！!？?；;：:、"'“”‘’「」『』《》〈〉（）()【】[\]…—\-~～·]+/gu;
const hasEntryKey = (part: string) => part.replace(BULLET, '').replace(SELF_LABEL, '').replace(IGNORABLE, '') !== '';

/** Mirrors the server `_split_line`: entries split on 「；」/「;」, rejoined with the line's own separator. */
function splitEntries(line: string): { separator: string; entries: string[] } {
  const separator = line.includes('；') || !line.includes(';') ? '；' : ';';
  return { separator, entries: line.split(/[；;]/).map(part => part.trim()).filter(hasEntryKey) };
}

/** Proposal entries currently waiting for the author, in stored order, as stored. */
export function pendingProposalEntries(notes: string | null | undefined): string[] {
  const seen = new Set<string>();
  for (const block of parseBlocks(notes ?? '')) {
    if (block.kind !== 'proposals') continue;
    for (const line of block.lines) splitEntries(line).entries.forEach(entry => seen.add(entry));
  }
  return [...seen];
}

/**
 * Removes the given pending entries from the proposal section. Returns the new text and the
 * removed entries grouped per original line (a fully removed line keeps its raw text), or null
 * when none of the entries is pending.
 */
function takeProposals(notes: string, entries: string[]) {
  const targets = new Set(entries.map(entry => entry.trim()).filter(Boolean));
  const blocks = parseBlocks(notes);
  const taken: string[] = [];
  let firstProposals = -1;
  blocks.forEach((block, index) => {
    if (block.kind !== 'proposals') return;
    if (firstProposals < 0) firstProposals = index;
    block.lines = block.lines.flatMap(line => {
      const { separator, entries: lineEntries } = splitEntries(line);
      const removed = lineEntries.filter(entry => targets.has(entry));
      if (removed.length === 0) return [line];
      const kept = lineEntries.filter(entry => !targets.has(entry));
      taken.push(kept.length === 0 ? line : removed.join(separator));
      return kept.length === 0 ? [] : [kept.join(separator)];
    });
  });
  if (taken.length === 0) return null;
  return { blocks, taken, firstProposals };
}

function finish(notes: string, blocks: Block[]): string {
  const kept = blocks.filter(block => block.kind !== 'proposals'
    || block.lines.some(line => splitEntries(line).entries.length > 0));
  const lines = joinBlocks(kept, '\n').split('\n');
  // Dropping a trailing proposal section must not leave its separating blank lines behind.
  const originalLines = notes.split(/\r?\n/);
  if (originalLines[originalLines.length - 1].trim()) {
    while (lines.length > 1 && !lines[lines.length - 1].trim()) lines.pop();
  }
  return lines.join(lineEnding(notes));
}

/** Human panel/card action: move these pending proposal entries into 【作者确认】. */
export function adoptProjectProposals(notes: string, entries: string[]): string | null {
  const taken = takeProposals(notes, entries);
  if (!taken) return null;
  const { blocks, firstProposals } = taken;
  const target = blocks.find(block => block.kind === 'confirmed');
  if (target) {
    let at = target.lines.length;
    while (at > 0 && !target.lines[at - 1].trim()) at--;
    target.lines.splice(at, 0, ...taken.taken);
  } else {
    const proposalsLeft = blocks.some(block => block.kind === 'proposals'
      && block.lines.some(line => splitEntries(line).entries.length > 0));
    blocks.splice(firstProposals, 0, {
      kind: 'confirmed', header: CONFIRMED_HEADER, lines: [...taken.taken, ...(proposalsLeft ? [''] : [])],
    });
  }
  return finish(notes, blocks);
}

/** Human panel/card action: drop these pending proposal entries. */
export function ignoreProjectProposals(notes: string, entries: string[]): string | null {
  const taken = takeProposals(notes, entries);
  return taken ? finish(notes, taken.blocks) : null;
}

/**
 * The author's own notes (unlabeled and confirmed entries) without section headers, in stored
 * order. The blank line that separates them from a following proposal section is not shown.
 */
export function authorNotesText(notes: string | null | undefined): string {
  const lines: string[] = [];
  for (const block of parseBlocks(notes ?? '')) {
    if (block.kind === 'proposals') {
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

const proposalSection = (notes: string) =>
  joinBlocks(parseBlocks(notes).filter(block => block.kind === 'proposals'), '\n');

/**
 * Stored notes after the author edited `authorNotesText(stored)` into `edited`. A line exactly
 * equal (trimmed) to a confirmed line of `stored` (or of `confirmedFrom`, e.g. the saved baseline)
 * stays confirmed; every other line is unlabeled. Order is kept by switching headers where the
 * source changes; the proposal section is kept unchanged after a blank line. A typed line shaped
 * like a section header stays author text (see `escapeAuthorLine`).
 * `authorNotesText(withAuthorNotes(s, e)) === e` and `withAuthorNotes(s, authorNotesText(s)) === s`;
 * typing back the author text of `confirmedFrom` (with the same proposals) returns it unchanged,
 * so a restored draft is not dirty even when the saved layout is not the canonical one.
 */
export function withAuthorNotes(stored: string, edited: string, confirmedFrom?: string): string {
  if (edited === authorNotesText(stored)) return stored;
  if (confirmedFrom !== undefined && edited === authorNotesText(confirmedFrom)
    && proposalSection(confirmedFrom) === proposalSection(stored)) return confirmedFrom;
  const confirmed = confirmedLines(stored);
  if (confirmedFrom) confirmedLines(confirmedFrom).forEach(line => confirmed.add(line));
  const proposals = parseBlocks(stored).filter(block => block.kind === 'proposals');
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
    if (proposals.length > 0) out.push('');
  }
  out.push(...proposals.flatMap(block => [block.header ?? '', ...block.lines]));
  return out.join(lineEnding(stored));
}

const CONFIRMED_HEADER = '【作者确认】';
const PROPOSALS_HEADER = '【AI 提议·待作者确认】';
const UNLABELED_HEADER = '【未标来源】';

/**
 * The server stores note sections under fixed Chinese headers and parses them back.
 * English readers see these labels instead; saving maps them back, so the stored text never changes shape.
 */
const ENGLISH_HEADER_LABELS: Record<string, string> = {
  [CONFIRMED_HEADER]: '[Confirmed by you]',
  [PROPOSALS_HEADER]: '[AI suggestions, not confirmed yet]',
  [UNLABELED_HEADER]: '[Saved notes]',
};
const STORED_HEADERS = Object.fromEntries(
  Object.entries(ENGLISH_HEADER_LABELS).map(([stored, label]) => [label, stored]),
);

function mapHeaderLines(notes: string, table: Record<string, string>): string {
  return notes.replace(/^[^\r\n]*$/gm, line => table[line.trim()] ?? line);
}

/** Notes as the author reads them: section headers in the interface language. */
export function displayProjectNotes(notes: string, language: string | undefined): string {
  return language?.toLowerCase().startsWith('en') ? mapHeaderLines(notes, ENGLISH_HEADER_LABELS) : notes;
}

/** Inverse of displayProjectNotes: what gets stored and compared. */
export function storedProjectNotes(displayed: string): string {
  return mapHeaderLines(displayed, STORED_HEADERS);
}
const HEADER_PREFIX = '^\\s*【\\s*(?:备注\\s*[·・]\\s*)?';
const HEADER_SUFFIX = '\\s*】\\s*(?:[（(].*[)）])?\\s*$';
const confirmedHeader = new RegExp(HEADER_PREFIX + '作者(?:已)?确认' + HEADER_SUFFIX);
const proposalsHeader = new RegExp(HEADER_PREFIX + 'AI\\s*提议[^】]*' + HEADER_SUFFIX, 'i');
const unlabeledHeader = new RegExp(HEADER_PREFIX + '未标来源' + HEADER_SUFFIX);

/** Human-only panel action on the existing source sections; never chat authority. */
export function adoptAllProjectProposals(notes: string): string | null {
  const blocks: Array<{ kind: 'unlabeled' | 'confirmed' | 'proposals'; header?: string; lines: string[] }> = [
    { kind: 'unlabeled', lines: [] },
  ];
  let current = blocks[0];
  for (const line of notes.split(/\r?\n/)) {
    const kind = confirmedHeader.test(line) ? 'confirmed'
      : proposalsHeader.test(line) ? 'proposals'
        : unlabeledHeader.test(line) ? 'unlabeled' : null;
    if (kind) {
      current = { kind, header: line, lines: [] };
      blocks.push(current);
    } else current.lines.push(line);
  }
  const proposals = blocks.filter(block => block.kind === 'proposals');
  const adoptedLines = proposals.flatMap(block => block.lines);
  if (!adoptedLines.some(line => line.trim())) return null;
  const target = blocks.find(block => block.kind === 'confirmed');
  if (target) target.lines.push(...adoptedLines);
  else {
    proposals[0].kind = 'confirmed';
    proposals[0].header = CONFIRMED_HEADER;
    proposals[0].lines = adoptedLines;
  }
  return blocks.filter(block => block.kind !== 'proposals')
    .flatMap(block => [...(block.header ? [block.header] : []), ...block.lines])
    .join(notes.includes('\r\n') ? '\r\n' : '\n');
}

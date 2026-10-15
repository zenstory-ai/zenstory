const CONFIRMED_HEADER = '【作者确认】';
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

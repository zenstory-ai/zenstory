const UUID_CORE =
  '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:-[a-z]+-folder)?';

const WRAPPED_ID_PATTERN = new RegExp(
  `[（(\\[]\\s*(?:(?:文件|目录|文件夹)?\\s*(?:id|file_id|folder_id|parent_id)\\s*[:=：]?\\s*)?\`?${UUID_CORE}\`?\\s*[\\])）]`,
  'gi',
);
const LABELLED_ID_PATTERN = new RegExp(
  `(?:(?:文件|目录|文件夹)\\s*)?\\b(?:id|file_id|folder_id|parent_id)\\s*[:=：]\\s*\`?${UUID_CORE}\`?`,
  'gi',
);
const BARE_UUID_PATTERN = new RegExp(`\`?${UUID_CORE}\`?`, 'gi');

const ID_COLUMN_HEADER = /^(?:(?:文件|目录|文件夹)\s*)?(?:id|uuid|file_id|folder_id|parent_id)$/i;
const PIPE_DIVIDER_CELL = /^:?-{2,}:?$/;

function stripInternalIds(line: string): string {
  return line
    .replace(WRAPPED_ID_PATTERN, '')
    .replace(LABELLED_ID_PATTERN, '')
    .replace(BARE_UUID_PATTERN, '')
    .replace(/[（(]\s*[)）]/g, '')
    .replace(/\s{2,}/g, (spaces, offset) => (offset === 0 ? spaces : ' '))
    .replace(/\s+([，。；：、,.])/g, '$1');
}

function splitPipeRow(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith('|') || !trimmed.endsWith('|') || trimmed.length < 3) {
    return null;
  }
  const inner = trimmed.slice(1, -1);
  return inner.split(/(?<!\\)\|/).map((cell) => cell.replace(/\\\|/g, '|').trim());
}

function isDividerRow(cells: string[]): boolean {
  return cells.length > 0 && cells.every((cell) => PIPE_DIVIDER_CELL.test(cell));
}

function convertPipeTable(headers: string[], rows: string[][]): string[] {
  const keepIndices = headers
    .map((header, idx) => ({ clean: stripInternalIds(header).replace(/[*`]/g, '').trim(), idx }))
    .filter(({ clean }) => !ID_COLUMN_HEADER.test(clean))
    .map(({ idx }) => idx);

  const bulletLines: string[] = [];
  for (const row of rows) {
    const parts: string[] = [];
    for (const idx of keepIndices) {
      const cleaned = stripInternalIds(row[idx] ?? '').trim();
      if (cleaned && cleaned !== '-' && cleaned !== '—') {
        parts.push(cleaned);
      }
    }
    if (parts.length === 0) continue;
    const [head, ...rest] = parts;
    const boldHead = head.startsWith('**') && head.endsWith('**') ? head : `**${head}**`;
    bulletLines.push(rest.length > 0 ? `- ${boldHead}：${rest.join(' · ')}` : `- ${boldHead}`);
  }
  return bulletLines;
}

/**
 * Formats a persisted or live compaction summary for author-facing display:
 * strips internal file/folder UUIDs and converts wide Markdown pipe tables
 * into compact vertical bullet rows that fit narrow mobile viewports.
 */
export function formatCompactionSummary(summary: string | undefined): string {
  const raw = summary?.trim();
  if (!raw) return '';

  const lines = raw.split(/\r?\n/);
  const output: string[] = [];
  let inFence = false;

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      output.push(line);
      continue;
    }
    if (inFence) {
      output.push(line);
      continue;
    }

    const headerCells = splitPipeRow(line);
    const dividerCells = i + 1 < lines.length ? splitPipeRow(lines[i + 1]) : null;
    if (
      headerCells &&
      dividerCells &&
      headerCells.length === dividerCells.length &&
      isDividerRow(dividerCells)
    ) {
      const dataRows: string[][] = [];
      let cursor = i + 2;
      while (cursor < lines.length) {
        const cells = splitPipeRow(lines[cursor]);
        if (!cells) break;
        dataRows.push(cells);
        cursor += 1;
      }
      output.push(...convertPipeTable(headerCells, dataRows));
      i = cursor - 1;
      continue;
    }

    const cleaned = stripInternalIds(line).trimEnd();
    if (/^\s*[-*+]\s*$/.test(cleaned)) continue;
    output.push(cleaned);
  }

  return output.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

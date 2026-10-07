import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DiffViewer } from '../DiffViewer';
import type { VersionComparison } from '../../types';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

const comparison: VersionComparison = {
  file_id: 'draft',
  version1: { number: 1, created_at: '2026-10-06T00:00:00Z', change_type: null, change_source: null, word_count: 3 },
  version2: { number: 2, created_at: '2026-10-06T00:00:01Z', change_type: null, change_source: null, word_count: 3 },
  unified_diff: '',
  html_diff: [
    { type: 'equal', old_line: 1, new_line: 1, content: 'Untouched first' },
    { type: 'removed', old_line: 2, new_line: null, content: 'Old paragraph' },
    { type: 'added', old_line: null, new_line: 2, content: 'New paragraph' },
    { type: 'equal', old_line: 3, new_line: 3, content: 'Untouched last' },
  ],
  stats: { lines_added: 1, lines_removed: 1, word_diff: 0 },
};

describe('DiffViewer', () => {
  it.each(['unified', 'split', 'inline'])('hides and restores unchanged lines in %s view', (mode) => {
    render(<DiffViewer comparison={comparison} />);
    fireEvent.click(screen.getByTitle(`editor:diff.${mode}View`));
    expect(screen.getAllByText('Untouched first')).not.toHaveLength(0);
    fireEvent.click(screen.getByRole('checkbox'));
    expect(screen.queryByText('Untouched first')).not.toBeInTheDocument();
    expect(screen.queryByText('Untouched last')).not.toBeInTheDocument();
    expect(screen.getByText('Old paragraph')).toBeInTheDocument();
    expect(screen.getByText('New paragraph')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(screen.getAllByText('Untouched first')).not.toHaveLength(0);
  });

  it('preserves paragraph separators and browser whitespace in inline view', () => {
    const { container } = render(<DiffViewer comparison={comparison} />);
    fireEvent.click(screen.getByTitle('editor:diff.inlineView'));
    const prose = container.querySelector('.leading-relaxed');
    expect(prose).toHaveClass('whitespace-pre-wrap');
    expect(prose?.textContent).toBe('Untouched first\nOld paragraph\nNew paragraph\nUntouched last\n');
    fireEvent.click(screen.getByRole('checkbox'));
    expect(prose?.textContent).toBe('Old paragraph\nNew paragraph\n');
  });
});

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DashboardSearchBar } from '../DashboardSearchBar';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => (key === 'common:clearSearch' ? 'Clear search' : key),
  }),
}));

describe('DashboardSearchBar', () => {
  it('hides the clear button when empty and shows it when non-empty', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <DashboardSearchBar value="" onChange={onChange} placeholder="Search projects" />,
    );

    expect(screen.queryByRole('button', { name: 'Clear search' })).not.toBeInTheDocument();

    rerender(<DashboardSearchBar value="dragon" onChange={onChange} placeholder="Search projects" />);
    const clearBtn = screen.getByRole('button', { name: 'Clear search' });
    expect(clearBtn).toBeInTheDocument();

    fireEvent.click(clearBtn);
    expect(onChange).toHaveBeenCalledWith('');
    expect(screen.getByRole('searchbox', { name: 'Search projects' })).toHaveFocus();
  });

  it('clears the search query on Escape key', () => {
    const onChange = vi.fn();
    render(<DashboardSearchBar value="dragon" onChange={onChange} placeholder="Search projects" />);

    const input = screen.getByRole('searchbox', { name: 'Search projects' });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onChange).toHaveBeenCalledWith('');
  });
});

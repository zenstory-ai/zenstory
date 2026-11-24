import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SkillsPane } from '../SkillsPane';
import type { Skill } from '../../../types';

const mockListSkills = vi.fn();
const mockSelectSkill = vi.fn();
const mockRemoveSkill = vi.fn();
let mockSelectedSkills: Array<{ id: string; name: string }> = [];

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options?.max !== undefined ? `${key}:${String(options.max)}` : key,
  }),
}));

vi.mock('../../../contexts/SkillTriggerContext', () => ({
  MAX_SELECTED_SKILLS: 3,
  useSkillTrigger: () => ({
    selectedSkills: mockSelectedSkills,
    selectSkill: mockSelectSkill,
    removeSkill: mockRemoveSkill,
  }),
}));

vi.mock('../../../lib/api', () => ({
  skillsApi: {
    list: () => mockListSkills(),
  },
}));

vi.mock('../../LazyMarkdown', () => ({
  LazyMarkdown: ({ children }: { children: string }) => <div>{children}</div>,
}));

vi.mock('../../../lib/logger', () => ({
  logger: { error: vi.fn() },
}));

const sampleSkills: Skill[] = [
  {
    id: 'skill-1',
    name: 'Dialogue Polish',
    description: 'Tighten character dialogue',
    triggers: ['dialogue', 'rewrite'],
    instructions: 'Make dialogue crisp.',
    source: 'user',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'skill-2',
    name: 'Minimal Skill',
    description: 'No triggers defined',
    triggers: [],
    instructions: 'Keep prose lean.',
    source: 'builtin',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

describe('SkillsPane', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSelectedSkills = [];
    mockListSkills.mockResolvedValue({ skills: sampleSkills });
  });

  it('toggles a selected skill off on second click in both list row and detail modal', async () => {
    mockSelectedSkills = [{ id: 'skill-1', name: 'Dialogue Polish' }];

    render(<SkillsPane />);
    const toggleBtn = await screen.findByRole('button', {
      name: 'editor:fileTree.removeSkill: Dialogue Polish',
    });
    expect(toggleBtn).toBeEnabled();
    expect(toggleBtn).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(toggleBtn);
    expect(mockRemoveSkill).toHaveBeenCalledWith('skill-1');

    // Opening the detail modal also offers a reversible remove button
    fireEvent.click(screen.getByRole('button', { name: 'Dialogue Polish' }));
    const modalRemove = await screen.findByRole('button', { name: 'editor:fileTree.removeSkill' });
    expect(modalRemove).toBeEnabled();
    fireEvent.click(modalRemove);
    expect(mockRemoveSkill).toHaveBeenCalledTimes(2);
  });

  it('hides the empty triggers section in detail modal when triggers are empty', async () => {
    render(<SkillsPane />);
    const minimalBtn = await screen.findByRole('button', { name: 'Minimal Skill' });
    fireEvent.click(minimalBtn);

    expect(await screen.findByText('editor:fileTree.skillInstructions')).toBeInTheDocument();
    expect(screen.queryByText('editor:fileTree.skillTriggers')).not.toBeInTheDocument();
  });

  it('keeps the disabled full-selection tooltip visible on hover instead of vanishing to opacity-0', async () => {
    mockSelectedSkills = [
      { id: 's-a', name: 'A' },
      { id: 's-b', name: 'B' },
      { id: 's-c', name: 'C' },
    ];

    render(<SkillsPane />);
    const useBtn = await screen.findByRole('button', {
      name: 'editor:fileTree.useSkill: Dialogue Polish',
    });
    expect(useBtn).toBeDisabled();
    expect(useBtn).toHaveAttribute('title', 'editor:fileTree.skillSelectionFull:3');
    expect(useBtn.className).toContain('group-hover:opacity-40');
    expect(useBtn.className).not.toContain('disabled:opacity-0');

    // Opening the detail modal at 3/3 also explains why the touch-sized Use button is disabled
    fireEvent.click(screen.getByRole('button', { name: 'Dialogue Polish' }));
    const modalUse = await screen.findByRole('button', { name: 'editor:fileTree.useSkill' });
    expect(modalUse).toBeDisabled();
    expect(modalUse).toHaveAttribute('title', 'editor:fileTree.skillSelectionFull:3');
    expect(modalUse.className).toContain('min-h-[44px]');
    expect(screen.getByText('editor:fileTree.skillSelectionFull:3')).toBeInTheDocument();
  });
});

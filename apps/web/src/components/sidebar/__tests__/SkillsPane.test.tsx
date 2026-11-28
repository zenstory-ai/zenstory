import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SkillsPane } from '../SkillsPane';
import { ApiError } from '../../../lib/apiClient';
import type { Skill } from '../../../types';

const mockListSkills = vi.fn();
const mockCreateSkill = vi.fn();
const mockSelectSkill = vi.fn();
const mockRemoveSkill = vi.fn();
const mockToastError = vi.fn();
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
    create: (payload: unknown) => mockCreateSkill(payload),
  },
}));

vi.mock('../../../lib/toast', () => ({
  toast: {
    error: (...args: unknown[]) => mockToastError(...args),
    success: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock('../../subscription/UpgradePromptModal', () => ({
  UpgradePromptModal: ({ open, title }: { open: boolean; title: string }) =>
    open ? <div role="dialog" data-testid="skill-quota-upgrade-modal">{title}</div> : null,
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

  it('quick-creates a custom skill in place without leaving the workbench and refreshes the list', async () => {
    const createdSkill: Skill = {
      id: 'skill-3',
      name: '章末留钩子',
      description: '每章末尾制造悬念',
      triggers: ['章末', '悬念'],
      instructions: '在最后一段留下未解危机。',
      source: 'user',
      is_active: true,
      created_at: '2026-01-02T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
    };
    mockListSkills
      .mockResolvedValueOnce({ skills: sampleSkills })
      .mockResolvedValueOnce({ skills: [...sampleSkills, createdSkill] });
    mockCreateSkill.mockResolvedValueOnce(createdSkill);

    const { container } = render(<SkillsPane />);
    await screen.findByText('Dialogue Polish');

    // No redundant w-3.5 dead left spacer inside skill rows
    expect(container.querySelector('.w-3\\.5')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'editor:fileTree.createSkill' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/skills:form\.name/), {
      target: { value: '章末留钩子' },
    });
    fireEvent.change(within(dialog).getByLabelText(/skills:form\.description/), {
      target: { value: '每章末尾制造悬念' },
    });
    fireEvent.change(within(dialog).getByLabelText(/skills:form\.triggers/), {
      target: { value: '章末，悬念' },
    });
    fireEvent.change(within(dialog).getByLabelText(/skills:form\.instructions/), {
      target: { value: '在最后一段留下未解危机。' },
    });

    fireEvent.click(within(dialog).getByRole('button', { name: 'common:save' }));

    await waitFor(() => {
      expect(mockCreateSkill).toHaveBeenCalledWith({
        name: '章末留钩子',
        description: '每章末尾制造悬念',
        triggers: ['章末', '悬念'],
        instructions: '在最后一段留下未解危机。',
      });
    });
    expect(await screen.findByText('章末留钩子')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens the upgrade prompt when quick-creating a skill hits ERR_QUOTA_EXCEEDED', async () => {
    mockCreateSkill.mockRejectedValueOnce(
      new ApiError(403, 'ERR_QUOTA_EXCEEDED'),
    );

    render(<SkillsPane />);
    await screen.findByText('Dialogue Polish');

    fireEvent.click(screen.getByRole('button', { name: 'editor:fileTree.createSkill' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/skills:form\.name/), {
      target: { value: '新技能' },
    });
    fireEvent.change(within(dialog).getByLabelText(/skills:form\.instructions/), {
      target: { value: '指令内容' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'common:save' }));

    expect(await screen.findByTestId('skill-quota-upgrade-modal')).toBeInTheDocument();
    expect(mockToastError).not.toHaveBeenCalled();
  });

  it('navigates to /skills from the header action and compact empty state inside a Router', async () => {
    mockListSkills.mockResolvedValue({ skills: [] });

    render(
      <MemoryRouter initialEntries={['/project/p1']}>
        <Routes>
          <Route path="/project/p1" element={<SkillsPane />} />
          <Route path="/skills" element={<div data-testid="skills-page">Skills Page</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('editor:fileTree.skillsEmpty')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'editor:fileTree.createSkill' })).toHaveLength(2);
    const discoverBtn = screen.getByRole('button', { name: 'editor:fileTree.discoverSkills' });

    fireEvent.click(discoverBtn);
    expect(await screen.findByTestId('skills-page')).toBeInTheDocument();
  });
});

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MaterialPreview } from '../MaterialPreview';
import type { MaterialPreviewResponse } from '../../lib/materialsApi';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
const preview: MaterialPreviewResponse = {
  title: '人物：沈砚', novel_title: '合成示例', suggested_file_type: 'character',
  suggested_folder_name: '角色', suggested_file_name: '沈砚.md',
  markdown: '# 沈砚\n\n离乡的修灯匠。\n始终在寻找兄长。\n\n**动机**\n\n- 旧信\n\n```text\n第一行\n第二行\n```\n\n<script>alert("unsafe")</script>',
};

describe('MaterialPreview', () => {
  it('shares safe reference typography without changing the three actions', async () => {
    const actions = { onAddToProject: vi.fn(), onAttachToChat: vi.fn(), onBack: vi.fn() };
    const { container } = render(<MaterialPreview preview={preview} isLoading={false} {...actions} />);
    const paragraph = await screen.findByText(/离乡的修灯匠/);
    expect(paragraph.textContent).toBe('离乡的修灯匠。\n始终在寻找兄长。');
    expect(paragraph.closest('.material-analysis .material-reader-prose')).not.toBeNull();
    expect(screen.getByText('动机').tagName).toBe('STRONG');
    expect(screen.getByRole('listitem')).toHaveTextContent('旧信');
    expect(container.querySelector('pre code')).toHaveTextContent('第一行');
    expect(container.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'editor:fileTree.addToProject' }));
    fireEvent.click(screen.getByRole('button', { name: 'editor:fileTree.attachToChat' }));
    fireEvent.click(screen.getByRole('button', { name: 'editor:fileTree.backToEdit' }));
    for (const action of Object.values(actions)) expect(action).toHaveBeenCalledTimes(1);
  });
  it('keeps loading separate from the preview and its actions', () => {
    const action = vi.fn();
    render(<MaterialPreview preview={preview} isLoading onAddToProject={action} onAttachToChat={action} onBack={action} />);
    expect(screen.getByText('common:loading')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByText('人物：沈砚')).not.toBeInTheDocument();
    expect(action).not.toHaveBeenCalled();
  });
});

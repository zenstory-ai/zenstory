import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import zhEditor from '../../../../public/locales/zh/editor.json';
import enEditor from '../../../../public/locales/en/editor.json';
import { SidebarTabs } from '../SidebarTabs';

const mocks = vi.hoisted(() => ({
  activeTab: 'files' as 'files' | 'skills' | 'materials' | 'dashboard',
  setActiveTab: vi.fn(),
  locale: 'zh' as 'zh' | 'en',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const locales = mocks.locale === 'zh' ? zhEditor : enEditor;
      const [, path] = key.split(':');
      const [section, field] = (path || key).split('.');
      const group = (locales as Record<string, Record<string, string>>)[section];
      return group?.[field] ?? key;
    },
  }),
}));

vi.mock('../../../stores/sidebarStore', () => ({
  useSidebarStore: () => ({
    activeTab: mocks.activeTab,
    setActiveTab: mocks.setActiveTab,
  }),
}));

describe('SidebarTabs', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.activeTab = 'files';
    mocks.locale = 'zh';
  });

  it('labels the third tab as 参考小说 in Chinese to distinguish it from the project 素材 folder', () => {
    render(<SidebarTabs />);

    expect(screen.getByRole('tab', { name: '项目文件' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '技能' })).toBeInTheDocument();
    const refTab = screen.getByRole('tab', { name: '参考小说' });
    expect(refTab).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '素材库' })).not.toBeInTheDocument();

    fireEvent.click(refTab);
    expect(mocks.setActiveTab).toHaveBeenCalledWith('materials');
  });

  it('labels the third tab as References in English without wrapping', () => {
    mocks.locale = 'en';
    render(<SidebarTabs />);

    expect(screen.getByRole('tab', { name: 'Files' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Skills' })).toBeInTheDocument();
    const refTab = screen.getByRole('tab', { name: 'References' });
    expect(refTab).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Materials' })).not.toBeInTheDocument();
    expect(refTab.querySelector('span')).toHaveClass('whitespace-nowrap');
  });
});

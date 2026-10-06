import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { AdminLayout } from '../AdminLayout';
import en from '../../../../public/locales/en/admin.json';
import zh from '../../../../public/locales/zh/admin.json';

vi.mock('../../../hooks/useMediaQuery', () => ({ useIsMobile: () => true }));
vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'admin', avatar_url: null } }),
}));
vi.mock('../../UserMenu', () => ({ UserAvatar: () => <span>avatar</span> }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback?: string) => fallback ?? _key }),
}));

describe('AdminLayout mobile navigation', () => {
  it('has localized mobile menu labels in both supported languages', () => {
    expect(en.header.openMenu).toBe('Open admin menu');
    expect(en.header.closeMenu).toBe('Close admin menu');
    expect(zh.header.openMenu).toBe('打开管理菜单');
    expect(zh.header.closeMenu).toBe('关闭管理菜单');
  });
  it('announces state, contains focus, closes on Escape, and makes the closed drawer inert', () => {
    render(
      <MemoryRouter initialEntries={['/admin']}>
        <Routes>
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<button>Page action</button>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    const trigger = screen.getByRole('button', { name: '打开管理菜单' });
    const drawer = document.getElementById('admin-mobile-navigation');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).toHaveAttribute('aria-controls', 'admin-mobile-navigation');
    expect(drawer).toHaveAttribute('inert');

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(drawer).not.toHaveAttribute('inert');
    expect(drawer).toHaveAttribute('role', 'dialog');
    expect(screen.getByRole('button', { name: '仪表盘' })).toHaveFocus();

    screen.getByRole('button', { name: '审计日志' }).focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(screen.getByRole('button', { name: '仪表盘' })).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).toHaveFocus();
    expect(drawer).toHaveAttribute('inert');
  });
});

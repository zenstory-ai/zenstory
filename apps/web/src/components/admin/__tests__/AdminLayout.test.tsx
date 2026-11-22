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
  it('lets the operator close the drawer from inside and return to the app on phones', () => {
    render(<MemoryRouter><AdminLayout /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: '打开管理菜单' }));
    expect(screen.getByRole('link', { name: '返回应用' })).toHaveAttribute('href', '/dashboard');
    fireEvent.click(screen.getByRole('button', { name: '关闭管理菜单' }));
    expect(screen.getByRole('button', { name: '打开管理菜单' })).toHaveFocus();
  });

  it('starts the next admin page at the top of its independent scroll container', () => {
    render(<MemoryRouter initialEntries={['/admin']}><Routes><Route path='/admin' element={<AdminLayout />}><Route index element={<div>Overview</div>} /><Route path='users' element={<div>Users page</div>} /></Route></Routes></MemoryRouter>);
    const main = screen.getByRole('main');
    main.scrollTop = 450;
    fireEvent.click(screen.getByRole('button', { name: '打开管理菜单' }));
    fireEvent.click(screen.getByRole('link', { name: '用户' }));
    expect(screen.getByText('Users page')).toBeInTheDocument();
    expect(main.scrollTop).toBe(0);
  });

  it('renders URL-backed sub-tabs for consolidated sections', () => {
    render(
      <MemoryRouter initialEntries={['/admin/subscriptions']}>
        <Routes>
          <Route path="/admin" element={<AdminLayout />}>
            <Route path="subscriptions" element={<div>Subscriptions view</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const subNav = screen.getByRole('navigation', { name: '营收' });
    expect(subNav).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '支付订单' })).toHaveAttribute('href', '/admin/payment-orders');
    expect(screen.getByRole('link', { name: '订阅用户' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: '兑换码' })).toHaveAttribute('href', '/admin/codes');
    expect(screen.getByRole('link', { name: '套餐配置' })).toHaveAttribute('href', '/admin/plans');
  });

  it('renders UX Health sub-tab under Overview without adding a top-level item', () => {
    render(
      <MemoryRouter initialEntries={['/admin/ux-health']}>
        <Routes>
          <Route path="/admin" element={<AdminLayout />}>
            <Route path="ux-health" element={<div>UX Health view</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const subNav = screen.getByRole('navigation', { name: '概览' });
    expect(subNav).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '仪表盘' })).toHaveAttribute('href', '/admin');
    expect(screen.getByRole('link', { name: '体验健康' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: '注册来源' })).toHaveAttribute('href', '/admin/acquisition');
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
    expect(screen.getByRole('link', { name: '概览' })).toHaveFocus();

    screen.getByRole('link', { name: '返回应用' }).focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(screen.getByRole('button', { name: '关闭管理菜单' })).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).toHaveFocus();
    expect(drawer).toHaveAttribute('inert');
  });
});

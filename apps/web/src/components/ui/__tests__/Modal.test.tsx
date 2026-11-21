import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Modal } from '../Modal';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe('Modal bodyClassName padding overrides', () => {
  it('uses responsive padding by default', () => {
    render(
      <Modal open onClose={vi.fn()} title="Default Modal">
        <div data-testid="modal-child">Body</div>
      </Modal>,
    );

    const bodyWrapper = screen.getByTestId('modal-child').parentElement as HTMLElement;
    expect(bodyWrapper).toHaveClass('px-4', 'sm:px-6', 'pb-4');
  });

  it('drops default responsive padding including sm:px-6 when bodyClassName passes p-0', () => {
    render(
      <Modal open onClose={vi.fn()} title="Full Bleed Modal" bodyClassName="p-0 overflow-hidden">
        <div data-testid="modal-child">Body</div>
      </Modal>,
    );

    const bodyWrapper = screen.getByTestId('modal-child').parentElement as HTMLElement;
    expect(bodyWrapper).toHaveClass('p-0', 'overflow-hidden');
    expect(bodyWrapper).not.toHaveClass('px-4');
    expect(bodyWrapper).not.toHaveClass('sm:px-6');
    expect(bodyWrapper).not.toHaveClass('pb-4');
  });

  it('drops only horizontal or bottom default padding when overridden individually', () => {
    const { rerender } = render(
      <Modal open onClose={vi.fn()} title="Horizontal Override" bodyClassName="px-2">
        <div data-testid="modal-child">Body</div>
      </Modal>,
    );

    let bodyWrapper = screen.getByTestId('modal-child').parentElement as HTMLElement;
    expect(bodyWrapper).toHaveClass('px-2', 'pb-4');
    expect(bodyWrapper).not.toHaveClass('px-4');
    expect(bodyWrapper).not.toHaveClass('sm:px-6');

    rerender(
      <Modal open onClose={vi.fn()} title="Bottom Override" bodyClassName="pb-0">
        <div data-testid="modal-child">Body</div>
      </Modal>,
    );

    bodyWrapper = screen.getByTestId('modal-child').parentElement as HTMLElement;
    expect(bodyWrapper).toHaveClass('px-4', 'sm:px-6', 'pb-0');
    expect(bodyWrapper).not.toHaveClass('pb-4');
  });
});

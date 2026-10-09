import { readFileSync } from 'node:fs';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Modal } from '../Modal';
import { ConfirmDialog } from '../ConfirmDialog';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_key: string, fallback?: string) => fallback ?? 'Close' }) }));

const zIndexOf = (source: string, pattern: RegExp): number => {
  const value = source.match(pattern)?.[1];
  expect(value, `z-index not found for ${pattern}`).toBeDefined();
  return Number(value);
};

describe('dialog stacking contract', () => {
  it('layers ConfirmDialog above Modal and below Toast', () => {
    const css = readFileSync('src/index.css', 'utf8');
    const confirmLayer = zIndexOf(css, /\.modal-overlay \{\s*@apply[^;]*\bz-\[(\d+)\]/);
    const modalLayer = zIndexOf(readFileSync('src/components/ui/Modal.tsx', 'utf8'), /overlayClasses = `[^`]*\bz-\[(\d+)\]/);
    const toastLayer = zIndexOf(readFileSync('src/components/Toast.tsx', 'utf8'), /fixed bottom-20[^"]*\bz-\[(\d+)\]/);

    expect(confirmLayer).toBeGreaterThan(modalLayer);
    expect(confirmLayer).toBeLessThan(toastLayer);
  });

  // A nested ConfirmDialog's portal can land before its Modal's portal in the
  // DOM, so document order cannot be relied on; the overlay class must lift it.
  it('renders a ConfirmDialog opened inside a Modal on the confirmation layer', () => {
    const tree = (confirmOpen: boolean) => (
      <Modal open title="History" onClose={vi.fn()}>
        <button>Restore</button>
        <ConfirmDialog open={confirmOpen} title="Restore?" message="Proceed?" onClose={vi.fn()} onConfirm={vi.fn()} />
      </Modal>
    );
    const view = render(tree(false));
    view.rerender(tree(true));

    const confirmOverlay = screen.getByRole('dialog', { name: 'Restore?' }).parentElement!;
    expect(confirmOverlay).toHaveClass('modal-overlay');
    expect(confirmOverlay.className).not.toMatch(/\bz-/);
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });
});

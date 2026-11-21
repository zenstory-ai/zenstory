import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Modal } from '../Modal';
import { ConfirmDialog } from '../ConfirmDialog';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_key: string, fallback?: string) => fallback ?? 'Close' }) }));

afterEach(() => { document.body.style.overflow = ''; });

describe('dialog keyboard contract', () => {
  it('contains forward/backward Tab in Modal while retaining input priority', () => {
    render(<Modal open title="Edit" onClose={vi.fn()}><input aria-label="Name" /><button>Save</button></Modal>);
    expect(screen.getByRole('textbox')).toHaveFocus();
    screen.getByRole('button', { name: 'Save' }).focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(screen.getByRole('button', { name: 'Save' })).toHaveFocus();
  });

  it('contains Tab in ConfirmDialog and ignores Escape while loading', () => {
    const onClose = vi.fn();
    const view = render(<ConfirmDialog open title="Delete" message="Really?" onClose={onClose} onConfirm={vi.fn()} />);
    screen.getByRole('button', { name: 'Confirm' }).focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(screen.getByRole('button', { name: 'Confirm' })).toHaveFocus();
    view.rerender(<ConfirmDialog open loading title="Delete" message="Really?" onClose={onClose} onConfirm={vi.fn()} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(screen.getByRole('dialog')).toHaveFocus();
  });

  it('preserves an outer scroll lock and the original trigger through rerenders', () => {
    const trigger = document.createElement('button');
    document.body.appendChild(trigger); trigger.focus();
    document.body.style.overflow = 'hidden';
    const props = { open: true, title: 'Confirm', message: 'Proceed?', onConfirm: vi.fn() };
    const view = render(<ConfirmDialog {...props} onClose={() => undefined} />);
    view.rerender(<ConfirmDialog {...props} onClose={() => undefined} />);
    view.rerender(<ConfirmDialog {...props} open={false} onClose={() => undefined} />);
    expect(document.body.style.overflow).toBe('hidden');
    expect(trigger).toHaveFocus();
    trigger.remove();
  });

  it('keeps nested dialog Escape and scroll locking scoped to the topmost dialog', () => {
    const onOuterClose = vi.fn();
    const onInnerClose = vi.fn();
    const view = render(
      <>
        <Modal open title="Outer" onClose={onOuterClose}><button>Outer action</button></Modal>
        <ConfirmDialog open title="Inner" message="Proceed?" onClose={onInnerClose} onConfirm={vi.fn()} />
      </>
    );

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onInnerClose).toHaveBeenCalledOnce();
    expect(onOuterClose).not.toHaveBeenCalled();

    view.rerender(<Modal open title="Outer" onClose={onOuterClose}><button>Outer action</button></Modal>);
    expect(document.body.style.overflow).toBe('hidden');
    view.unmount();
    expect(document.body.style.overflow).toBe('');
  });

  it('uses viewport-overlay and responsive touch buttons in ConfirmDialog', () => {
    render(<ConfirmDialog open variant="danger" title="Delete" message="Proceed?" onClose={vi.fn()} onConfirm={vi.fn()} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog.parentElement).toHaveClass('viewport-overlay');
    const confirmBtn = screen.getByRole('button', { name: 'Confirm' });
    expect(confirmBtn.className).toContain('min-h-[44px]');
    expect(confirmBtn.className).toContain('text-[hsl(var(--primary-foreground))]');
  });
});

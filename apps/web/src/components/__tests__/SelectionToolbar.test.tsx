import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SelectionToolbar } from '../SelectionToolbar';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe('SelectionToolbar', () => {
  const originalInnerWidth = window.innerWidth;
  const originalInnerHeight = window.innerHeight;

  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', { value: 390, writable: true, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 844, writable: true, configurable: true });
  });

  afterEach(() => {
    Object.defineProperty(window, 'innerWidth', { value: originalInnerWidth, writable: true, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: originalInnerHeight, writable: true, configurable: true });
  });

  it('clamps near-edge coordinates inside the viewport with a 12px margin', () => {
    const onAdd = vi.fn();
    const onClose = vi.fn();

    const { rerender } = render(
      <SelectionToolbar text="selected" position={{ x: 0, y: 0 }} onAdd={onAdd} onClose={onClose} />,
    );

    const toolbar = screen.getByRole('button', { name: 'input.addToContext' }).closest('div.fixed') as HTMLElement;
    expect(Number.parseFloat(toolbar.style.left)).toBeGreaterThanOrEqual(12);
    expect(Number.parseFloat(toolbar.style.top)).toBeGreaterThanOrEqual(12);

    rerender(
      <SelectionToolbar text="selected" position={{ x: 500, y: 1200 }} onAdd={onAdd} onClose={onClose} />,
    );
    expect(Number.parseFloat(toolbar.style.left)).toBeLessThanOrEqual(390 - 12);
    expect(Number.parseFloat(toolbar.style.top)).toBeLessThanOrEqual(844 - 12);
  });

  it('adds quote and closes on button click, and closes on Escape', () => {
    const onAdd = vi.fn();
    const onClose = vi.fn();

    render(
      <SelectionToolbar text="selected" position={{ x: 200, y: 200 }} onAdd={onAdd} onClose={onClose} />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'input.addToContext' }));
    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

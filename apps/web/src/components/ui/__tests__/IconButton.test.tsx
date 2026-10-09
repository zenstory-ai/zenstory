import { createRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { IconButton } from '../IconButton'

describe('IconButton', () => {
  it('is named by its label, so an icon-only control is never announced as an empty button', () => {
    render(<IconButton label="删除项目" icon={<svg aria-hidden="true" />} />)

    expect(screen.getByRole('button', { name: '删除项目' })).toHaveAttribute('title', '删除项目')
  })

  it('does not submit the surrounding form', () => {
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault())
    render(
      <form onSubmit={onSubmit}>
        <IconButton label="展开" icon={<svg aria-hidden="true" />} />
      </form>,
    )

    fireEvent.click(screen.getByRole('button', { name: '展开' }))

    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('forwards its ref so menus can return focus to the trigger', () => {
    const ref = createRef<HTMLButtonElement>()
    render(<IconButton ref={ref} label="打开菜单" icon={<svg aria-hidden="true" />} />)

    expect(ref.current).toBe(screen.getByRole('button', { name: '打开菜单' }))
  })

  // Two text-colour utilities on one element resolve by stylesheet order, not class order,
  // so a caller's colour must never sit next to a tone's colour.
  const textColours = (el: HTMLElement) =>
    el.className.split(/\s+/).filter((c) => /^text-\[hsl\(var\(--/.test(c))

  it('plain tone leaves the text colour to the caller, so a custom colour wins cleanly', () => {
    render(
      <IconButton
        label="收藏"
        tone="plain"
        className="text-[hsl(var(--accent-primary))]"
        icon={<svg aria-hidden="true" />}
      />,
    )

    expect(textColours(screen.getByRole('button', { name: '收藏' }))).toEqual([
      'text-[hsl(var(--accent-primary))]',
    ])
  })

  it.each(['default', 'strong', 'danger'] as const)('%s tone sets exactly one text colour', (tone) => {
    render(<IconButton label="操作" tone={tone} icon={<svg aria-hidden="true" />} />)

    expect(textColours(screen.getByRole('button', { name: '操作' }))).toHaveLength(1)
  })
})

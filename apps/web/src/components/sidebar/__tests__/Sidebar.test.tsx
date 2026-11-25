import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

let mockActiveTab: 'files' | 'skills' | 'materials' | 'dashboard' = 'files'

vi.mock('../../../stores/sidebarStore', () => ({
  useSidebarStore: () => ({
    activeTab: mockActiveTab,
  }),
}))

vi.mock('../SidebarTabs', () => ({
  SidebarTabs: () => <div data-testid="sidebar-tabs">SidebarTabs</div>,
}))

vi.mock('../FileTreePane', () => ({
  FileTreePane: () => <div data-testid="file-tree-pane">FileTreePane</div>,
}))

vi.mock('../SkillsPane', () => ({
  SkillsPane: () => <div data-testid="skills-pane">SkillsPane</div>,
}))

vi.mock('../MaterialsPane', () => ({
  MaterialsPane: () => <div data-testid="materials-pane">MaterialsPane</div>,
}))

import { Sidebar } from '../Sidebar'

describe('Sidebar lazy tab panes', () => {
  beforeEach(() => {
    mockActiveTab = 'files'
  })

  it('renders FileTreePane eagerly on default files tab without mounting hidden panes', () => {
    render(<Sidebar />)

    expect(screen.getByTestId('file-tree-pane')).toBeInTheDocument()
    expect(screen.queryByTestId('skills-pane')).not.toBeInTheDocument()
    expect(screen.queryByTestId('materials-pane')).not.toBeInTheDocument()
  })

  it('lazy-loads SkillsPane when switching to the skills tab', async () => {
    mockActiveTab = 'skills'
    render(<Sidebar />)

    expect(await screen.findByTestId('skills-pane')).toBeInTheDocument()
    expect(screen.queryByTestId('file-tree-pane')).not.toBeInTheDocument()
    expect(screen.queryByTestId('materials-pane')).not.toBeInTheDocument()
  })

  it('lazy-loads MaterialsPane when switching to the materials tab', async () => {
    mockActiveTab = 'materials'
    render(<Sidebar />)

    expect(await screen.findByTestId('materials-pane')).toBeInTheDocument()
    expect(screen.queryByTestId('file-tree-pane')).not.toBeInTheDocument()
    expect(screen.queryByTestId('skills-pane')).not.toBeInTheDocument()
  })
})

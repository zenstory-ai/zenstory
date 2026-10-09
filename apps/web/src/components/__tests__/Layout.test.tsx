import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import * as React from 'react'
import { MemoryRouter } from 'react-router-dom'

// Mock dependencies BEFORE importing Layout
const mockOpenSearch = vi.fn()
const mockCloseSearch = vi.fn()
const mockSetActivePanel = vi.fn()
const mockSwitchToEditor = vi.fn()

// Create mutable mock values
let mockIsMobile = false
let mockIsTablet = false
let mockActivePanel: 'files' | 'editor' | 'chat' = 'editor'
let mockCurrentProjectId: string | null = null

// Mock hooks
vi.mock('../../hooks/useMediaQuery', () => ({
   
  useIsMobile: vi.fn(() => mockIsMobile),
  useIsTablet: vi.fn(() => mockIsTablet),
}))

vi.mock('../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({
    activePanel: mockActivePanel,
    setActivePanel: mockSetActivePanel,
    switchToEditor: mockSwitchToEditor,
    switchToFiles: vi.fn(),
    switchToChat: vi.fn(),
    isMobile: mockIsMobile,
  }),
  MobileLayoutProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}))

vi.mock('../../contexts/FileSearchContext', () => ({
  useFileSearchContext: () => ({
    isSearchOpen: false,
    openSearch: mockOpenSearch,
    closeSearch: mockCloseSearch,
    toggleSearch: vi.fn(),
  }),
  FileSearchProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProjectId: mockCurrentProjectId,
    project: null,
    isLoading: false,
    error: null,
    refetchProject: vi.fn(),
    selectedFile: null,
    setSelectedFile: vi.fn(),
    createProject: vi.fn(),
    updateProject: vi.fn(),
    deleteProject: vi.fn(),
  }),
  ProjectProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}))

vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  useMaterialAttachment: () => ({
    addMaterial: vi.fn(),
    removeMaterial: vi.fn(),
  }),
  MaterialAttachmentProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}))

vi.mock('../Header', () => ({
  Header: () => React.createElement('header', { 'data-testid': 'header' }, 'Header'),
}))

vi.mock('../BottomTabs', () => ({
  BottomTabs: ({
    activeTab,
    onTabChange,
  }: {
    activeTab: string
    onTabChange: (tab: string) => void
  }) =>
    React.createElement(
      'nav',
      { 'data-testid': 'bottom-tabs' },
      React.createElement('button', { onClick: () => onTabChange('files') }, 'Files'),
      React.createElement('button', { onClick: () => onTabChange('editor') }, 'Editor'),
      React.createElement('button', { onClick: () => onTabChange('chat') }, 'Chat'),
      React.createElement('span', null, `Active: ${activeTab}`)
    ),
}))

vi.mock('react-resizable-panels', () => ({
  Panel: ({ children, defaultSize, minSize }: { children: React.ReactNode; defaultSize?: number | string; minSize?: number | string }) =>
    React.createElement('div', { 'data-testid': 'panel', 'data-default-size': defaultSize, 'data-min-size': minSize }, children),
  Group: ({ children }: { children: React.ReactNode }) =>
    React.createElement('div', { 'data-testid': 'group' }, children),
  Separator: () => React.createElement('div', { 'data-testid': 'separator' }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) =>
      typeof fallback === 'string' ? fallback : key,
  }),
}))

// Import Layout after mocks are set up
import { Layout } from '../Layout'

const leftPanel = React.createElement('div', { 'data-testid': 'left-panel' }, 'Left Panel')
const middlePanel = React.createElement('div', { 'data-testid': 'middle-panel' }, 'Middle Panel')
const rightPanel = React.createElement('div', { 'data-testid': 'right-panel' }, 'Right Panel')

// Helper function to render with Router context (needed for SidebarTabs which uses useNavigate)
const renderWithRouter = (ui: React.ReactElement) => {
  const Wrapper = ({ children }: { children: React.ReactElement }) => (
    <MemoryRouter initialEntries={['/project/test-project-id']}>
      {children}
    </MemoryRouter>
  )
  return {
    ...render(ui, { wrapper: Wrapper }),
    rerenderWithRouter: (newUi: React.ReactElement) => {
      return render(newUi, { wrapper: Wrapper })
    }
  }
}

describe('Layout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockIsMobile = false
    mockIsTablet = false
    mockActivePanel = 'editor'
    mockCurrentProjectId = null
  })

  afterEach(() => {
    mockIsMobile = false
    mockIsTablet = false
    mockActivePanel = 'editor'
  })

  describe('Desktop Layout', () => {
    it('uses explicit percentage defaults and usable pixel minimums under panel API v4', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)
      const panels = screen.getAllByTestId('panel')
      expect(panels.map((panel) => panel.dataset.defaultSize)).toEqual(['20%', '48%', '32%'])
      expect(panels.map((panel) => panel.dataset.minSize)).toEqual(['180', '30%', '300'])
    })

    it('keeps tablet panels usable without treating percentages as pixels', () => {
      mockIsTablet = true
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)
      const panels = screen.getAllByTestId('panel')
      expect(panels.map((panel) => panel.dataset.defaultSize)).toEqual(['25%', '40%', '35%'])
      expect(panels.map((panel) => panel.dataset.minSize)).toEqual(['20%', '30%', '280'])
    })

    it('renders three-panel layout', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      expect(screen.getByTestId('header')).toBeInTheDocument()
      // Desktop layout uses Sidebar component (not left prop), editor-panel, and chat-panel
      expect(screen.getByTestId('editor-panel')).toBeInTheDocument()
      expect(screen.getByTestId('chat-panel')).toBeInTheDocument()
      // middle and right panels are nested inside editor-panel and chat-panel
      expect(screen.getByTestId('middle-panel')).toBeInTheDocument()
      expect(screen.getByTestId('right-panel')).toBeInTheDocument()
    })

    it('renders panels in correct order', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const panels = screen.getAllByTestId('panel')
      expect(panels).toHaveLength(3)
    })

    it('renders separators between panels', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const separators = screen.getAllByTestId('separator')
      expect(separators).toHaveLength(2)
    })

    it('renders header at the top', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const header = screen.getByTestId('header')
      expect(header.tagName).toBe('HEADER')
    })

    it('contains group element for panel layout', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const group = screen.getByTestId('group')
      expect(group).toBeInTheDocument()
    })

    it('has proper panel structure', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const panels = screen.getAllByTestId('panel')
      expect(panels).toHaveLength(3)

      // First panel contains Sidebar (not left prop), middle panel contains editor, right panel contains chat
      expect(panels[0]).toBeInTheDocument() // Sidebar
      expect(panels[1]).toHaveTextContent('Middle Panel')
      expect(panels[2]).toHaveTextContent('Right Panel')
    })

    it('listens for Cmd+K keyboard shortcut', async () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      fireEvent.keyDown(window, { metaKey: true, key: 'k' })

      await waitFor(() => {
        expect(mockOpenSearch).toHaveBeenCalled()
      })
    })

    it('listens for Ctrl+K keyboard shortcut', async () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      fireEvent.keyDown(window, { ctrlKey: true, key: 'k' })

      await waitFor(() => {
        expect(mockOpenSearch).toHaveBeenCalled()
      })
    })

    it('does not trigger search on regular key press', async () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      // Clear any previous calls
      mockOpenSearch.mockClear()

      fireEvent.keyDown(window, { key: 'k' })

      // Wait a bit to ensure no call is made
      await new Promise(resolve => setTimeout(resolve, 100))

      expect(mockOpenSearch).not.toHaveBeenCalled()
    })

    it('does not trigger search on Cmd+other key', async () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      // Clear any previous calls
      mockOpenSearch.mockClear()

      fireEvent.keyDown(window, { metaKey: true, key: 'p' })

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(mockOpenSearch).not.toHaveBeenCalled()
    })

    it('does not render bottom tabs in desktop mode', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      expect(screen.queryByTestId('bottom-tabs')).not.toBeInTheDocument()
    })
  })

  describe('Mobile Layout', () => {
    beforeEach(() => {
      mockIsMobile = true
    })

    it('opens on the AI tab when the project was started from a dashboard idea', () => {
      mockCurrentProjectId = 'project-with-idea'
      localStorage.setItem('zenstory_inspiration_project-with-idea', JSON.stringify({
        content: '写一篇反转短篇',
        projectType: 'short',
        timestamp: Date.now(),
      }))
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      expect(mockSetActivePanel).toHaveBeenCalledWith('chat')
    })

    it('stays on the editor when there is no idea waiting to be sent', () => {
      mockCurrentProjectId = 'project-with-idea'
      // A stale idea (older than 5 minutes) is never sent, so no reason to switch.
      localStorage.setItem('zenstory_inspiration_project-with-idea', JSON.stringify({
        content: '写一篇反转短篇',
        timestamp: Date.now() - 10 * 60 * 1000,
      }))
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      expect(mockSetActivePanel).not.toHaveBeenCalled()
    })

    it('renders mobile layout when isMobile is true', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      expect(screen.getByTestId('header')).toBeInTheDocument()
      expect(screen.getByTestId('bottom-tabs')).toBeInTheDocument()
    })

    it('renders only active panel in mobile mode', () => {
      mockActivePanel = 'editor'
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      // In mobile layout, all panels are rendered but hidden with CSS (opacity-0, aria-hidden)
      // The active panel should be visible (not aria-hidden), inactive panels should be hidden
      const middlePanelElement = screen.getByTestId('middle-panel')
      expect(middlePanelElement).toBeInTheDocument()
      expect(middlePanelElement.closest('[aria-hidden="true"]')).toBeNull()

      // Inactive panels exist in DOM but are hidden
      const leftPanelElement = screen.queryByTestId('left-panel')
      if (leftPanelElement) {
        expect(leftPanelElement.closest('[aria-hidden="true"]')).not.toBeNull()
      }

      const rightPanelElement = screen.queryByTestId('right-panel')
      if (rightPanelElement) {
        expect(rightPanelElement.closest('[aria-hidden="true"]')).not.toBeNull()
      }

      const panels = screen.getAllByRole('tabpanel', { hidden: true })
      expect(panels).toHaveLength(3)
      expect(document.getElementById('editor-panel')).toHaveAttribute('aria-labelledby', 'editor-tab')
      expect(document.getElementById('editor-panel')).not.toHaveAttribute('inert')
      expect(document.getElementById('files-panel')).toHaveAttribute('inert')
      expect(document.getElementById('chat-panel')).toHaveAttribute('inert')
    })

    it('shows files panel when activePanel is files', () => {
      mockActivePanel = 'files'
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      // Files panel in mobile layout uses MobileFileTree, not the left prop
      // Check that inactive panels are hidden (aria-hidden)
      const middlePanelElement = screen.queryByTestId('middle-panel')
      if (middlePanelElement) {
        expect(middlePanelElement.closest('[aria-hidden="true"]')).not.toBeNull()
      }

      const rightPanelElement = screen.queryByTestId('right-panel')
      if (rightPanelElement) {
        expect(rightPanelElement.closest('[aria-hidden="true"]')).not.toBeNull()
      }
    })

    it('shows chat panel when activePanel is chat', () => {
      mockActivePanel = 'chat'
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      // Chat panel should be visible (not aria-hidden)
      const rightPanelElement = screen.getByTestId('right-panel')
      expect(rightPanelElement).toBeInTheDocument()
      expect(rightPanelElement.closest('[aria-hidden="true"]')).toBeNull()

      // Inactive panels should be hidden
      const leftPanelElement = screen.queryByTestId('left-panel')
      if (leftPanelElement) {
        expect(leftPanelElement.closest('[aria-hidden="true"]')).not.toBeNull()
      }

      const middlePanelElement = screen.queryByTestId('middle-panel')
      if (middlePanelElement) {
        expect(middlePanelElement.closest('[aria-hidden="true"]')).not.toBeNull()
      }
    })

    it('renders bottom navigation tabs', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      expect(screen.getByTestId('bottom-tabs')).toBeInTheDocument()
      expect(screen.getByText(/Active: editor/i)).toBeInTheDocument()
    })

    it('calls setActivePanel when tab is clicked', async () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const filesButton = screen.getByRole('button', { name: 'Files' })
      fireEvent.click(filesButton)

      await waitFor(() => {
        expect(mockSetActivePanel).toHaveBeenCalledWith('files')
      })
    })

    it('listens for Cmd+K keyboard shortcut in mobile mode', async () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      fireEvent.keyDown(window, { metaKey: true, key: 'k' })

      await waitFor(() => {
        expect(mockOpenSearch).toHaveBeenCalled()
      })
    })

    it('listens for Ctrl+K keyboard shortcut in mobile mode', async () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      fireEvent.keyDown(window, { ctrlKey: true, key: 'k' })

      await waitFor(() => {
        expect(mockOpenSearch).toHaveBeenCalled()
      })
    })

    it('removes keyboard event listener on unmount in mobile mode', () => {
      const removeEventListenerSpy = vi.spyOn(window, 'removeEventListener')

      const { unmount } = renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      unmount()

      expect(removeEventListenerSpy).toHaveBeenCalledWith('keydown', expect.any(Function))

      removeEventListenerSpy.mockRestore()
    })
  })

  describe('Responsive Behavior', () => {
    it('switches to mobile layout when isMobile becomes true', () => {
      mockIsMobile = false
      const { rerenderWithRouter } = renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      // Desktop mode - no bottom tabs
      expect(screen.queryByTestId('bottom-tabs')).not.toBeInTheDocument()

      // Switch to mobile
      mockIsMobile = true
      rerenderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      // Mobile mode - has bottom tabs
      expect(screen.getByTestId('bottom-tabs')).toBeInTheDocument()
    })
  })

  describe('Accessibility', () => {
    it('has proper document structure', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      expect(screen.getByTestId('header')).toBeInTheDocument()
    })

    it('maintains focus management', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      // Focus should not be trapped
      expect(document.activeElement).toBe(document.body)
    })

    it('mobile layout has proper document structure', () => {
      mockIsMobile = true
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      expect(screen.getByTestId('header')).toBeInTheDocument()
      expect(screen.getByTestId('bottom-tabs')).toBeInTheDocument()
    })
  })

  describe('Cleanup', () => {
    it('removes keyboard event listener on unmount', () => {
      const removeEventListenerSpy = vi.spyOn(window, 'removeEventListener')

      const { unmount } = renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      unmount()

      expect(removeEventListenerSpy).toHaveBeenCalledWith('keydown', expect.any(Function))

      removeEventListenerSpy.mockRestore()
    })
  })

  describe('Styling', () => {
    it('has full screen container', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const header = screen.getByTestId('header')
      const container = header.closest('div')
      expect(container).toHaveClass('h-dvh')
      expect(container).not.toHaveClass('w-screen')
      expect(container).toHaveClass('inset-0')
    })

    it('has fixed positioning', () => {
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const header = screen.getByTestId('header')
      const container = header.closest('div')
      expect(container).toHaveClass('fixed')
      expect(container).toHaveClass('inset-0')
    })

    it('mobile layout has full screen container', () => {
      mockIsMobile = true
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const header = screen.getByTestId('header')
      const container = header.closest('div')
      expect(container).toHaveClass('h-dvh')
      expect(container).not.toHaveClass('w-screen')
      expect(container).toHaveClass('inset-0')
      expect(container).toHaveClass('fixed')
    })

    it('mobile layout has padding for bottom tabs', () => {
      mockIsMobile = true
      renderWithRouter(<Layout left={leftPanel} middle={middlePanel} right={rightPanel} />)

      const mainElement = screen.getByTestId('middle-panel').closest('main')
      expect(mainElement).toHaveClass('pb-14')
    })
  })
})

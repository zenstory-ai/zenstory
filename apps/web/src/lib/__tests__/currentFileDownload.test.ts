import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const flushOpenEditor = vi.fn()
const getFile = vi.fn()
vi.mock('../editorSaveTracker', () => ({ flushOpenEditor: () => flushOpenEditor() }))
vi.mock('../api', () => ({ fileApi: { get: (id: string) => getFile(id) } }))

import { currentFileDownloadName, downloadCurrentFile, UnsavedEditorError } from '../currentFileDownload'

describe('downloadCurrentFile (#276: one file, copied by the program)', () => {
  let clicked: { href: string; download: string }[]

  beforeEach(() => {
    clicked = []
    flushOpenEditor.mockReset()
    getFile.mockReset()
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.href, download: this.download })
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('names the file after its title, without characters a file system rejects', () => {
    expect(currentFileDownloadName('第3章：雨/夜?\t')).toBe('第3章：雨 夜.txt')
    expect(currentFileDownloadName('a:b')).toBe('a b.txt')
    expect(currentFileDownloadName('  ')).toBe('untitled.txt')
  })

  it('saves the open editor first, then downloads the saved text of that one file', async () => {
    flushOpenEditor.mockReturnValue(Promise.resolve({ saved: true, fileId: 'f1' }))
    getFile.mockResolvedValue({ id: 'f1', title: '第1章 雨夜', content: '雨还在下。' })

    const result = await downloadCurrentFile('f1')

    expect(getFile).toHaveBeenCalledWith('f1')
    expect(result.filename).toBe('第1章 雨夜.txt')
    expect(clicked).toEqual([{ href: 'blob:x', download: '第1章 雨夜.txt' }])
  })

  it('does not hand out an older copy when the open editor could not save', async () => {
    flushOpenEditor.mockReturnValue(Promise.resolve({ saved: false, fileId: 'f1' }))

    await expect(downloadCurrentFile('f1')).rejects.toBeInstanceOf(UnsavedEditorError)
    expect(getFile).not.toHaveBeenCalled()
    expect(clicked).toEqual([])
  })

  it('ignores an unsaved editor showing a different file', async () => {
    flushOpenEditor.mockReturnValue(Promise.resolve({ saved: false, fileId: 'other' }))
    getFile.mockResolvedValue({ id: 'f1', title: '大纲', content: '一、开端' })

    await downloadCurrentFile('f1')

    expect(clicked).toHaveLength(1)
  })

  it('reports a failed read instead of claiming success', async () => {
    flushOpenEditor.mockReturnValue(null)
    getFile.mockRejectedValue(new Error('404'))

    await expect(downloadCurrentFile('gone')).rejects.toThrow('404')
    expect(clicked).toEqual([])
  })
})

import { describe, expect, it, vi, afterEach } from 'vitest'
import { downloadBlob } from '../downloadBlob'

describe('downloadBlob', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('creates an object URL, clicks a temporary download anchor, and revokes the URL', () => {
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:download-test')
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const clicked: Array<{ href: string; download: string }> = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.href, download: this.download })
    })

    const blob = new Blob(['hello'], { type: 'text/plain' })
    downloadBlob(blob, 'chapter.txt')

    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect(clicked).toEqual([{ href: 'blob:download-test', download: 'chapter.txt' }])
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:download-test')
    expect(document.querySelectorAll('a[download]')).toHaveLength(0)
  })
})

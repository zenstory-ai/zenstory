import { describe, expect, it } from 'vitest'
import { parseContentDispositionFilename } from '../contentDisposition'

describe('parseContentDispositionFilename', () => {
  it('decodes the RFC 5987 name the export API sends', () => {
    const header = `attachment; filename*=UTF-8''${encodeURIComponent('雨夜_正文.txt')}`
    expect(parseContentDispositionFilename(header)).toBe('雨夜_正文.txt')
  })

  it('prefers filename* over filename regardless of order and keeps later params out', () => {
    expect(
      parseContentDispositionFilename(
        `attachment; filename*=utf-8''%E8%8A%82%E5%A5%8F.zip; filename="skill.zip"`,
      ),
    ).toBe('节奏.zip')
    expect(
      parseContentDispositionFilename(`attachment; filename="skill.zip"; filename*=UTF-8''%E8%8A%82%E5%A5%8F.zip`),
    ).toBe('节奏.zip')
  })

  it('accepts a language tag and quoted extended values', () => {
    expect(parseContentDispositionFilename(`attachment; filename*="UTF-8'zh-CN'%E6%AD%A3%E6%96%87.txt"`)).toBe('正文.txt')
  })

  it('reads a plain quoted or token filename', () => {
    expect(parseContentDispositionFilename('attachment; filename="drafts.txt"')).toBe('drafts.txt')
    expect(parseContentDispositionFilename('attachment; filename=drafts.txt; size=12')).toBe('drafts.txt')
    expect(parseContentDispositionFilename('attachment; filename="a \\"b\\".txt"')).toBe('a "b".txt')
  })

  it('falls back to filename when filename* is malformed', () => {
    expect(parseContentDispositionFilename(`attachment; filename*=UTF-8''%E0%A4%A; filename="safe.txt"`)).toBe('safe.txt')
  })

  it('strips path segments and returns null when nothing usable is present', () => {
    expect(parseContentDispositionFilename('attachment; filename="../../etc/passwd"')).toBe('passwd')
    expect(parseContentDispositionFilename('attachment')).toBeNull()
    expect(parseContentDispositionFilename(null)).toBeNull()
    expect(parseContentDispositionFilename('attachment; filename=""')).toBeNull()
  })
})

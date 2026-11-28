import { describe, it, expect } from 'vitest'
import { countWords } from '../documentChunker'

describe('countWords', () => {
  it('counts English words', () => {
    expect(countWords('Hello world test')).toBe(3)
  })

  it('counts Chinese characters', () => {
    expect(countWords('你好世界')).toBe(4)
  })

  it('counts mixed content', () => {
    expect(countWords('Hello 世界')).toBe(3) // 1 English + 2 Chinese
    expect(countWords('Hello世界')).toBe(3) // adjacent Latin + CJK still separated cleanly
  })

  it('counts words accurately across 10+ Unicode scripts without splitting on diacritics', () => {
    // Russian (Cyrillic)
    expect(countWords('Привет мир, это тест')).toBe(4)
    // Arabic (with and without harakat combining marks)
    expect(countWords('مرحبا بالعالم الجميل')).toBe(3)
    expect(countWords('مَرْحَباً بِالْعَالَمِ')).toBe(2)
    // Korean (Hangul, space-delimited eojeol)
    expect(countWords('안녕하세요 세계 입니다')).toBe(3)
    // Polish (extended Latin diacritics)
    expect(countWords('żółć gęślą jaźń')).toBe(3)
    // Vietnamese (NFC and NFD combining marks)
    expect(countWords('người Việt Nam')).toBe(3)
    expect(countWords('người Việt Nam'.normalize('NFD'))).toBe(3)
    // Portuguese
    expect(countWords('ação coração pão')).toBe(3)
    // Spanish
    expect(countWords('niño mañana canción')).toBe(3)
    // French
    expect(countWords('café résumé naïve')).toBe(3)
    // Indonesian
    expect(countWords('Selamat pagi dunia')).toBe(3)
    // Japanese (Hiragana/Katakana + Han counted per character)
    expect(countWords('東京カフェ')).toBe(5)
  })

  it('ignores pure numbers and punctuation', () => {
    expect(countWords('12345 !@# ... —')).toBe(0)
  })

  it('handles empty string', () => {
    expect(countWords('')).toBe(0)
  })

  it('handles whitespace-only string', () => {
    expect(countWords('   ')).toBe(0)
  })
})

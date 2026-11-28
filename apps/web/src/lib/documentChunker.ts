/**
 * Unicode-aware word counting utility for multilingual manuscripts.
 */

/**
 * Scripts counted per character (Han ideographs and Japanese Hiragana/Katakana).
 * Korean Hangul is space-delimited (eojeol) and is therefore counted by word runs.
 */
export const CJK_CHAR_RE = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/gu;

/**
 * Word runs for all non-CJK alphabetic/syllabic scripts (Latin including extended
 * diacritics for pl/vi/pt/es/fr/id/en, Cyrillic for ru, Arabic, Hangul for ko,
 * Greek, Devanagari, etc.), keeping combining marks attached to their base letter.
 */
export const NON_CJK_WORD_RE = /\p{L}[\p{L}\p{M}]*/gu;

/**
 * Count words in a document across CJK and Unicode alphabetic scripts.
 *
 * Counting methodology:
 * - Han ideographs and Hiragana/Katakana: each character = 1 word
 * - All other alphabetic/syllabic scripts (en, id, pt, es, fr, pl, vi, ru, ar, ko, …):
 *   each contiguous letter/combining-mark run = 1 word (diacritics do not split words)
 * - Numbers and punctuation/symbols are not counted
 *
 * @param content - Document content to count words in
 * @returns Total word count
 */
export function countWords(content: string): number {
  if (!content) return 0;
  const normalized = content.normalize('NFC');
  const cjkChars = (normalized.match(CJK_CHAR_RE) || []).length;
  const withoutCjk = normalized.replace(CJK_CHAR_RE, ' ');
  const nonCjkWords = (withoutCjk.match(NON_CJK_WORD_RE) || []).length;
  return cjkChars + nonCjkWords;
}

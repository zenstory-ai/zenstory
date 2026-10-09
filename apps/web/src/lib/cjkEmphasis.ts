/**
 * Bold next to Chinese punctuation, e.g. `这是**《余额》**的开头`.
 *
 * CommonMark only lets `**` open when it is "left-flanking" and close when it is
 * "right-flanking". A run between a letter and punctuation (`是**《`, `》**的`)
 * qualifies for only one side, because the rules assume a space would separate
 * words — Chinese has none. The model writes this constantly and readers saw the
 * asterisks verbatim.
 *
 * Fix without a new parser: before parsing, put a zero-width space between such a
 * `**` run and the punctuation, which makes the run usable on both sides; after
 * parsing, `remarkStripCjkEmphasisMarkers` removes those zero-width spaces again
 * so nothing invisible ends up in the rendered or copied text.
 */

const ZWSP = "\u200B";

const PUNCTUATION = /[\p{P}\p{S}]/u;
const WHITESPACE = /\s/u;
// Han, CJK symbols and punctuation, fullwidth forms, kana, Hangul.
const CJK = /[\u2E80-\u2FFF\u3000-\u303F\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFFEF\u{20000}-\u{2FFFF}]/u;

// `**` or `***`, not part of a longer run, not escaped.
const STRONG_RUN = /(?<![*\\])\*{2,3}(?!\*)/g;

const charBefore = (text: string, index: number): string | undefined => {
  if (index <= 0) return undefined;
  const code = text.charCodeAt(index - 1);
  if (code >= 0xdc00 && code <= 0xdfff && index >= 2) return text.slice(index - 2, index);
  return text[index - 1];
};

const charAt = (text: string, index: number): string | undefined => {
  if (index >= text.length) return undefined;
  const codePoint = text.codePointAt(index);
  return codePoint === undefined ? undefined : String.fromCodePoint(codePoint);
};

const isWordChar = (ch: string | undefined) => ch !== undefined && !WHITESPACE.test(ch) && !PUNCTUATION.test(ch);
const isPunct = (ch: string | undefined) => ch !== undefined && PUNCTUATION.test(ch);
const isCjk = (ch: string | undefined) => ch !== undefined && CJK.test(ch);

/** Inserts zero-width spaces so `**` beside CJK punctuation can open and close. */
export function prepareCjkEmphasis(markdown: string): string {
  if (!markdown.includes("**")) return markdown;
  return markdown.replace(STRONG_RUN, (run: string, offset: number, whole: string) => {
    const before = charBefore(whole, offset);
    const after = charAt(whole, offset + run.length);
    if (!isCjk(before) && !isCjk(after)) return run;
    // `是**《`: could close but not open.
    if (isWordChar(before) && isPunct(after)) return `${run}${ZWSP}`;
    // `》**的`: could open but not close.
    if (isPunct(before) && isWordChar(after)) return `${ZWSP}${run}`;
    return run;
  });
}

type MdNode = { type: string; value?: unknown; children?: MdNode[] };

const stripMarkers = (node: MdNode) => {
  if (
    (node.type === "text" || node.type === "inlineCode" || node.type === "code") &&
    typeof node.value === "string" &&
    node.value.includes(ZWSP)
  ) {
    node.value = node.value.split(ZWSP).join("");
  }
  node.children?.forEach(stripMarkers);
};

/** Remark plugin: removes the zero-width spaces `prepareCjkEmphasis` added. */
export function remarkStripCjkEmphasisMarkers() {
  return (tree: MdNode) => {
    stripMarkers(tree);
  };
}

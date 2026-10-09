/**
 * The chat "write chapter 1" next step (NextStepCard): which wording a project
 * gets, whether the author already said 「先不用」 for it, and which suggestion
 * chips say the same thing as the card.
 */

export type NextStepKind = "novel" | "short" | "screenplay";

export const nextStepKind = (projectType: string | undefined | null): NextStepKind =>
  projectType === "screenplay" || projectType === "short" ? projectType : "novel";

const DISMISSED_KEY_PREFIX = "zenstory_next_step_dismissed_";

/** 「先不用」 is remembered per project in this browser; storage errors mean "not dismissed". */
export function isNextStepDismissed(projectId: string): boolean {
  try {
    return localStorage.getItem(`${DISMISSED_KEY_PREFIX}${projectId}`) === "1";
  } catch {
    return false;
  }
}

export function rememberNextStepDismissed(projectId: string): void {
  try {
    localStorage.setItem(`${DISMISSED_KEY_PREFIX}${projectId}`, "1");
  } catch {
    // Private mode / blocked storage: the card stays hidden for this page view only.
  }
}

// "写第一章…" / "先写第1集剧本…" / "开始写正文" / "动笔写开篇…" / "开始创作第一章"
const WRITE_FIRST_UNIT_ZH = /(?:写|动笔|开写|创作)[^，。,.!?！？]{0,4}?(?:第\s*[一1１]\s*[章集]|正文|开篇)/;
// "写第一章前先补人物小传": chapter 1 is only the time reference, the chip offers something else.
const BEFORE_FIRST_UNIT = /(?:第\s*[一1１]\s*[章集]|正文|开篇)(?:之)?前/;
const WRITE_FIRST_UNIT_EN = /\b(?:write|start|draft)\b.{0,20}\b(?:chapter (?:1|one)|episode (?:1|one)|the opening|the story)\b/i;
// "先补第一章细纲" / "写第一章大纲" plan chapter 1 rather than write it ("按大纲写第一章" still writes).
const PLANNING = /细纲|梗概|(?:补|改|调|完善|细化|写|出)[^，。,.]{0,4}大纲|\boutline (?:for|of)\b|\b(?:revise|adjust|refine|expand)\b.*\boutline\b/i;

/**
 * True when a suggestion chip only repeats the next-step card ("write chapter
 * 1"). While the card is shown it is the single entry for that action; chips
 * keep the other directions (补角色、改大纲…).
 */
export function duplicatesNextStep(suggestion: string): boolean {
  const text = suggestion.trim();
  if (!text || PLANNING.test(text) || BEFORE_FIRST_UNIT.test(text)) return false;
  return WRITE_FIRST_UNIT_ZH.test(text) || WRITE_FIRST_UNIT_EN.test(text);
}

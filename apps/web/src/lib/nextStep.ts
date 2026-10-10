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

// "写第一章…" / "先写第1集剧本…" / "开始写正文" / "动笔写开篇…" / "开始创作第一章" / "写第一篇"
const WRITE_FIRST_UNIT_ZH = /(?:写|动笔|开写|创作)[^，。,.!?！？]{0,4}?(?:第\s*[一1１]\s*[章集篇]|正文|开篇)/;
// "开始第一章" / "进入正文": no writing verb, but the unit follows the start word directly.
// Kept adjacent so "开始设计开篇钩子" (a different task) is not caught.
const START_FIRST_UNIT_ZH = /(?:开始|着手|进入)(?:第\s*[一1１]\s*[章集篇]|正文)/;
// "写第一章前先补人物小传": chapter 1 is only the time reference, the chip offers something else.
const BEFORE_FIRST_UNIT = /(?:第\s*[一1１]\s*[章集篇]|正文|开篇)(?:之)?前/;
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
  return WRITE_FIRST_UNIT_ZH.test(text) || START_FIRST_UNIT_ZH.test(text) || WRITE_FIRST_UNIT_EN.test(text);
}

/** Fields a workflow stop carries, live (stream item) or replayed (status card). */
interface WorkflowStopLike {
  type: string;
  reason?: string;
  question?: string;
  message?: string;
  details?: string[];
}

/**
 * A workflow stop the chat renders as 「等你回复」: the AI is waiting for the
 * author's answer. Same rule for live stream items and persisted status cards.
 */
export function isClarificationStop(item: WorkflowStopLike): boolean {
  if (item.type !== "workflow_stopped" || item.reason === "user_stopped") return false;
  if (item.reason === "clarification_needed") return true;
  const text = (value: unknown) => (typeof value === "string" ? value.trim() : "");
  const question = text(item.question) || text(item.message);
  const details = (item.details ?? []).some((d) => text(d));
  return !item.reason && Boolean(question || details);
}

const ROUND_END_TYPES = new Set(["workflow_stopped", "workflow_complete", "iteration_exhausted"]);

const lastRoundEnd = <T extends WorkflowStopLike>(items: readonly T[] | undefined): T | undefined =>
  [...(items ?? [])].reverse().find((item) => ROUND_END_TYPES.has(item.type));

interface ChatMessageLike {
  role: string;
  displayItems?: readonly WorkflowStopLike[];
  statusCards?: readonly WorkflowStopLike[];
}

/**
 * True while the latest round ended by asking the author something (a structured
 * clarification), so the "write chapter 1" card must not compete with that question:
 * its fixed request would not answer it. Uses the in-flight / just-ended round's
 * stream items first, then the last saved message (history replay keeps the card).
 * Plain-text offers like 「要我开始写第一章吗？」 are not clarification stops and keep the card.
 */
export function latestRoundAwaitsReply(
  messages: readonly ChatMessageLike[],
  streamItems?: readonly WorkflowStopLike[],
): boolean {
  const streamEnd = lastRoundEnd(streamItems);
  if (streamEnd) return isClarificationStop(streamEnd);
  const last = messages[messages.length - 1];
  if (!last || last.role !== "assistant") return false;
  // Same source MessageList renders: the saved timeline when there is one, else the status cards.
  const end = lastRoundEnd(last.displayItems?.length ? last.displayItems : last.statusCards);
  return end ? isClarificationStop(end) : false;
}

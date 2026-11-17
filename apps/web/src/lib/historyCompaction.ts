/**
 * History compaction (single loop agent, between rounds). The server sends a `thinking` event
 * with one of these steps around the summary call, then `history_compacted` when it succeeded;
 * the saved round records `{type: "history_compacted"}` in its display events.
 */
export const HISTORY_COMPACTION_STEP = "history_compaction";
export const HISTORY_COMPACTION_SKIPPED_STEP = "history_compaction_skipped";

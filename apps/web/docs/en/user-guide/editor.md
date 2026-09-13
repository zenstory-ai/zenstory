# Edit and review text in ZenStory

ZenStory's editor supports direct text editing, precise quotations to chat, a selected-text **Humanize** action, paragraph-level diff review, explicit saving and retained version history. The important distinction is that saving the current file and creating a history snapshot are related but separate operations.

For the complete station lost-property example, start with [Write your first short story in ZenStory](https://zenstory.ai/docs/getting-started/first-project). For conversation scope, attached context and file-tool behavior, see [AI Assistant](https://zenstory.ai/docs/user-guide/ai-assistant).

## Write and save manually

Open a file rather than a folder in the project tree. The editor exposes a title field and a plain-text content area. Type or paste your revision, then check the status bar before switching files.

- After a change, the editor schedules an automatic save after about three seconds without further editing.
- Use **Save** or `Ctrl/Command + S` when you need to submit immediately.
- **Unsaved**, **Saving**, and the last-saved time describe the current file state. They do not prove that every keystroke has its own recoverable version.
- The editor pauses its automatic save while AI is editing the same file, reducing the risk that an older local copy overwrites the AI update.

A save writes the current title and content. A version is a separate snapshot: small edits can be saved with no snapshot, and a full version-history quota can also cause the content to save while the snapshot is skipped. Before a risky rewrite, keep a separate copy of the passage and wait for the saved state. The editor does not promise offline recovery.

The status-bar count is a workbench measure, not a publishing-platform count. Its current function counts each supported Chinese character and each contiguous Latin-letter sequence; numbers and symbols are excluded. Check the destination's own rules before submitting a manuscript.

## Quote a passage for a narrow request

For a local change, select the exact text in the editor. Use the floating **Add to context** action or `Ctrl/Command + Shift + Q`. The quotation carries its source file into chat. Then state both the permitted change and what must remain unchanged.

Choose the handoff you actually want:

- **Suggestion only:** “Return one replacement paragraph in chat. Do not modify files.” You decide whether to paste it.
- **Apply to the file:** name the file and ask the agent to replace only the quoted passage. The agent can operate project files; do not assume every file write waits for a confirmation dialog.

The focused file can supply saved file content to the assistant within its context budget, but a quotation makes the exact target explicit. Save or preserve current local edits before relying on focused-file context.

## Use selected-text Humanize separately

Select text, then choose **Humanize** or press `Ctrl/Command + Shift + R`. This invokes the dedicated selected-text rewrite path and opens diff review with the previous file and proposed replacement. It is not the same as asking chat to discuss a paragraph, nor does it establish authorship or guarantee detector scores. This guide source-checked the flow but did not call the AI service, so it does not present the example below as model output.

Established facts: the inner-pocket description is wrong, Lin He does not know why, and the bag has not been released.

**Original editing example:**

```text
Before: Shen Min named the object in the inner pocket. Lin He immediately knew she was lying.
After:  Lin He did not hand over the gray canvas bag.
        “Take another moment—what was in the inner pocket?”
```

**Reason:** a wrong answer does not become proof of a lie. Withholding the bag and asking again moves the scene while leaving the cause open. The question is an editorial phrasing choice, not new backstory or an imposed solution.

## Review a diff before finishing

Diff review can open from more than one route:

1. **Humanize** builds a proposed replacement for the selected text.
2. A streamed chat `edit_file` result can open review when both the original and changed content are available.
3. A save conflict opens review between the newer server content and your unsaved local candidate.

Review is not a universal pre-write approval gate. File creation, other tool results, or an edit without comparable before-and-after content may follow a different path.

The review queue groups changes by paragraph. Inspect each old/new pair, then **Accept**, **Reject**, or reset your decision. Crucially, **Pending is applied by default** when you finish; reject every change you do not want. **Accept all** and **Reject all** are available for deliberate bulk decisions. Choose **Finish review** or **Apply changes** to write the reconstructed result.

Verified review shortcuts apply when the review surface has focus: `Y` accepts the current item, `N` rejects it, `U` or `R` resets it, arrow keys or `J`/`K` move between items, and `L` locates it in the diff. `Shift + Y` accepts all, `Shift + N` rejects all, and `Enter` finishes. To discard the candidate changes, choose **Reject all**, check the decisions, then finish. Dismissing a view is not proof of undo.

## Handle a save conflict without discarding work

If AI or another tab changed the file after you loaded it, your save can be rejected as stale. ZenStory keeps the local text in the editor and opens diff review using the newer server text as the baseline and your local text as the candidate. First preserve any irreplaceable local passage; do not refresh the page or clear browser data as a troubleshooting shortcut. Review both sides and finish only after deciding what to keep.

If another write lands while you are applying the reviewed result, the editor reopens review against the newest server content instead of silently discarding the result. A quota notice saying the content saved without a version snapshot is different from a save conflict: the current file was saved, but that save did not add a retained history entry.

## Reviewed source

Source checked on 2026-09-12. These references describe a fixed version; no real-account editing or model generation was performed.

- [Text selection and adding an exact quotation to chat](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L310-L354)
- [Selected-text Humanize request and transition into diff review](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L363-L445)
- [Debounced/manual saving and editor/review shortcuts](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L509-L675)
- [Loading the focused file and related files into context](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/context/assembler.py#L168-L177)
- [Chat file-edit completion entering diff review when comparable content exists](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/hooks/useChatStreaming.ts#L1200-L1297)
- [Pending, accepted and rejected review state; pending applies by default](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/contexts/ProjectContext.tsx#L221-L315)
- [Per-change review navigation and shortcuts](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/DiffReviewSplitView.tsx#L209-L244)
- [Manual save outcomes, stale-write handling and version-quota notice](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Editor.tsx#L297-L393)
- [Applying reviewed content and reopening review after a second conflict](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Editor.tsx#L397-L475)
- [Mixed Chinese/English workbench word-count function](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/lib/documentChunker.ts#L26-L29)

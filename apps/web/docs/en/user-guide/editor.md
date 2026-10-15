# Using the editor

Open a project file to write, edit its title, quote a selection, or review AI changes. The editor uses plain text and shows save status at the bottom.

## Save the current file

Autosave runs after about three seconds without typing. Choose **Save** or press `Command/Ctrl + S` to submit immediately.

- Unsaved: the current text has not been confirmed on the server.
- Saving: the submission is still pending.
- Saved or a save time: the submitted title and text are saved.

Autosave pauses while AI edits the same file or diff review remains open. Finish that round before checking the current draft.

Consecutive manual edits close together may merge into one history version. When the allowance is full and merging is unavailable, your prose is still saved without adding a version. See [version history](version-history.md).

## Quote and revise a passage

Select text and choose **Add to chat**, or press `Command/Ctrl + Shift + Q`. The quote includes its source file.

```text
Check only the quoted passage. The character does not know the owner's identity yet.
Identify any sentence that exceeds this knowledge and suggest a replacement.
Keep the bag at the desk; do not change other paragraphs.
```

For direct editing, name the target, replacement scope, and facts to retain. For suggestions only, say “Reply in chat only; do not change files.”

**Humanize**, or `Command/Ctrl + Shift + R`, generates a candidate rewrite for the selection and opens review. It helps revise expression; it cannot guarantee detector scores.

## Review changes

Read each old/new pair, then accept, reject, or reset. Pending changes are adopted when you finish. Explicitly reject changes you do not want.

Use **Accept all** or **Reject all** after deciding the whole batch. Finish review, then inspect the combined text and save status. Some file operations write directly, so diff review is not a universal approval step before every write.

## Draft recovery and conflicts

The browser attempts to retain unsaved drafts before leaving or a page update. When reopening the same account, project, and file:

- If server content is unchanged, the draft returns to the editor. Check it and save.
- If server content has changed, compare both copies and choose whether to restore the local draft or use the server copy.
- If local storage fails, follow the message and keep important text separately.

A save conflict means the file changed while you were editing. Retain local text, compare the newer server copy, and merge. Reloading or clearing site data can affect local recovery; do not start there.

## Word count

The workbench counts Chinese characters and continuous English-letter sequences, excluding numbers and punctuation. Submission platforms can count differently; check their rules before delivery.

Next: [AI assistant](ai-assistant.md) and [keyboard shortcuts](../reference/keyboard-shortcuts.md).

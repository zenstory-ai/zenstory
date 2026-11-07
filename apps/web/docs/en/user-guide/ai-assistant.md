# AI writing assistant

The right-hand AI assistant can brainstorm story directions, draft prose, check setting consistency, and directly read, create, or edit project files. State what to do, which files to rely on, and where to stop each turn.

## Describe one clear task

A clear request names the target file, established facts, permitted changes, and stopping point (up to 20,000 characters per message):

```text
Read Scene 1 and Three-scene outline first.
Write Scene 2 into the current draft file, in Lin He's limited viewpoint.
The bag remains at the desk; Lin He does not know the cause of the mistake.
Stop when Shen Min says “The bag isn't mine.” Do not write the ending.
```

For brainstorming, add “Give options only; do not change files.” When asking the AI to write into a file, specify whether to replace or append so a scene is not written twice.

## Supply context

| Evidence | How to provide it and limits |
| --- | --- |
| **Current file** | Open the target file in the editor; the focused file identifies saved content for the AI to read |
| **Characters, outlines, earlier chapters** | Name the files to read in your prompt, or highlight key lines in the editor and click **Quote** (`Ctrl/Cmd + Shift + Q`, up to **5** quotes per message) |
| **Project materials** | In **Files → Materials**, click **Add to chat** on a material file (up to **5** material attachments per message) |
| **Reference Library items** | In the project's left **Materials** tab, expand an analyzed reference novel and click **Add to chat** on an entry (shares the **5**-item per-message limit) |
| **Long-term rules and conventions** | Maintain **Writing Conventions** in **AI Memory** at the top of chat (or ask the AI in chat to “remember …”) |

Save recent edits in the editor before sending your message. Each turn operates within a context budget, so attach only the items relevant to the current task. If two files share a similar title, ask the AI to list matching files before picking the target.

## Skills and generation modes

- **Attach writing skills**: Type `/` in the chat input to open the skill picker, or click `+` next to a skill in the project's left **Skills** tab. You can explicitly attach up to **3** skills per turn, and then describe your task in the input box. Even when you do not pick a skill manually, the AI can load enabled skills as needed based on their name and description.
- **Switch generation modes**: Use the selector in the bottom-left corner of the chat input:
  - **Fast** (default): Writes without an automatic post-draft review pass for faster turnaround—ideal for brainstorming, setting setup, and quick scenes.
  - **Quality**: Automatically reviews and revises the draft when needed, which takes longer—suited for key chapters and final passes.

## Check file operation cards and steer active runs

- **Inspect file cards**: Whenever the AI reads, creates, or edits project files, operation cards appear in its reply. Click a file card to open that file in the center editor and verify the result, or click **Undo** on an edit card to revert that file change. If candidate prose appears only inside the chat reply without a file card, it has not been written to a project file yet.
- **Steer or stop mid-run**: One generation task can run per account at a time. While the AI is generating, you can type follow-up instructions into the input box (**Add to your request**) and click **Add request**—the interface reports whether your note was received into the current run. To halt output immediately, click **Stop generating** in the bottom right; file edits completed before stopping remain saved.

## Continue after an interruption

If a run stops early due to a network drop or manual stop, open the target file and check the turn's operation cards to see where the text ended before sending a scoped continuation prompt:

```text
Read the current Scene 2 and list what is complete and what is missing.
Keep existing paragraphs and fill only the gap; do not append the whole scene again.
```

Use the inline retry action only when nothing was written to your files and the interface offers a retry button. Do not repeatedly send the same prompt while your connection is unstable.

## Start a new chat and message quotas

Click **New chat** in the top-right header of the chat pane to start a fresh conversation without deleting project files or **AI Memory**. Before switching chats, save any accepted outline or character decisions into your project files, then open the target file and state your goal and evidence in the new chat.

Free accounts include **3** AI messages per day (resetting at 00:00 Beijing time, UTC+8; free accounts on the same network also share a daily free network allowance), while Pro includes unlimited daily messages. When the system determines that a turn produced no substantive output and refunds the message, the interface notes that the turn was not counted toward today's allowance. See [Plans & Benefits](billing-benefits.md) for full details.

Related: [Using the editor](editor.md), [AI Memory and context](../advanced/ai-memory.md), and [Writing workflow](../advanced/workflow-tips.md).

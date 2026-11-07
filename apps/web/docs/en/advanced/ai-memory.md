# AI Memory and context

**AI Memory** stores your project's short summary, writing style, current phase, and cross-chat Writing Conventions. Keep detailed character sheets, full outlines, and chapter prose in your project files, and specify which files each turn should read.

## View and edit AI Memory

Click **AI Memory** in the top header of the project chat pane, edit the fields, and click **Save** at the bottom; to close the dialog without saving your edits, click the close button (`×`) in the top-right corner. Reopen the dialog after major updates to verify what is stored.

| Field | What to record | Limit |
| --- | --- | --- |
| **Project Summary** | Genre, central situation, viewpoint, and story direction | 4,000 characters |
| **Writing Style** | Pacing, prose preferences, and habits to avoid | 1,000 characters |
| **Current Phase** | What is saved so far and the immediate next task | 1,000 characters |
| **Writing Conventions** | Character knowledge boundaries, plot rules, prohibitions, and lasting constraints | 4,000 characters |

You do not need to fill the character limits—put your highest-priority constraints first and keep detailed lore in project files:

- **Project Summary, Writing Style, and Current Phase**: You can edit these fields directly or ask the AI to update them in chat; inspect the stored values after major story decisions.
- **Writing Conventions (author-controlled)**: When you explicitly ask the AI in chat to “remember …” a rule, the AI appends that note to **Writing Conventions** and displays an **Added to Writing Conventions** card in chat—click **View and edit** on the card to open **AI Memory** focused directly on **Writing Conventions**, where you can edit or delete the note. Otherwise, the AI never modifies **Writing Conventions** on its own; editing or deleting existing conventions is always done by you in **AI Memory**.
- **Concurrent update protection**: If **Writing Conventions** are updated elsewhere while you have **AI Memory** open with unsaved edits, clicking **Save** alerts you that **Writing Conventions changed elsewhere**. Click **View latest version** to inspect the newer text, then choose **Use latest version** or **Overwrite and save my version**.

## Separate established facts from progress

Keep author knowledge, character knowledge, and already-written events distinct. For example, after Scene 1 of *Before Closing* ends with the mismatched item description:

```text
Summary: Realist mystery in Lin He's limited viewpoint.
Style: Advance through questions and observable actions.
Current Phase: Scene 1 saved; the bag remains at the desk. Scene 2 is unwritten.
Writing Conventions: Lin does not know why the description is wrong. Shen's relationship
with the owner and the final recipient remain undecided.
Evidence: Lin He, Shen Min, Three-scene outline, Scene 1 — The claim.
```

Do not turn suspicion into an exposed fraud, and do not record the next scene's plan as already completed.

## Correct an outdated memory entry

Compare your prose and character files to find the outdated field, then update that part:

```text
Check AI Memory against Scene 1 and the character files.
List conflicting sentences, evidence, and replacements only.
Do not change files or memory yet.
```

Save the accepted correction in **AI Memory** yourself, or ask the AI to update **Project Summary**, **Writing Style**, or **Current Phase**. Updating AI Memory does not automatically rewrite saved prose—if the plot changes, decide separately which files need revision.

## Record progress before starting a new chat

Before clicking **New chat**, record your completed work, open decisions, and next step in **Current Phase**. Starting a new chat keeps your project's **AI Memory**, but it does not carry over every old chat message or the entire book in full. Open the target file and quote or name key evidence before continuing in the new chat.

Related: [AI writing assistant](../user-guide/ai-assistant.md) and [Writing workflow](workflow-tips.md).

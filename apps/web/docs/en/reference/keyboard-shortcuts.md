# Keyboard shortcuts

Shortcuts use `Command` (`⌘`) on macOS and `Ctrl` on Windows or Linux, and apply to whichever pane currently has focus. Pressing `Enter` while selecting an IME candidate confirms the characters without sending a chat message.

## Files and editor

| Shortcut | Action |
| --- | --- |
| `Command/Ctrl + K` | Search file titles and body text across the current project |
| `↑` / `↓` in search | Move through matching results |
| `Enter` / `Esc` in search | Open the selected result / close search |
| `Command/Ctrl + S` | Save the open file immediately |
| `Command/Ctrl + Shift + Q` | Quote the selected editor text into the AI chat input |
| `Command/Ctrl + Shift + R` | Smooth AI phrasing on the selection and open the Humanize result preview |

Undo (`Command/Ctrl + Z`) and redo follow your browser's standard text-editing shortcuts. Saving your text does not create a separate version-history entry every single time—consecutive manual saves within a short window merge automatically.

## AI chat input

| Key | Action |
| --- | --- |
| `Enter` | Send message |
| `Shift + Enter` | Insert a line break |
| `Tab` (when input is empty) | Fill in the first suggested prompt |
| `/` | Open the skill picker |
| `↑` / `↓` in the skill menu | Highlight a skill |
| `Enter` / `Tab` in the skill menu | Attach the highlighted skill |
| `Esc` in the skill menu | Close the skill menu |

After attaching a skill, type what you want the AI to work on before sending. Mobile on-screen keyboards vary by device—tap the **Send** button directly whenever `Enter` inserts a newline instead.

## Diff review

Click inside the diff review area first so keyboard shortcuts take effect:

| Key | Action |
| --- | --- |
| `↑` / `K`, `↓` / `J` | Jump to the previous or next change |
| `Y` / `N` | Accept or reject the active change |
| `U` or `R` | Reset the active change to pending |
| `L` | Scroll to the active change in the document |
| `Shift + Y` / `Shift + N` | Accept all or reject all changes |
| `Enter` | Finish review and apply results |

Any change still marked pending is **accepted** when you finish review. If you want to discard the proposed rewrite completely, press `Shift + N` (or click **Reject all**) before finishing.

## When a shortcut does not respond

Click the pane you want to control first, make sure an IME candidate box is not still open, and check whether the editor is currently in diff review mode—or click the matching toolbar button directly. ZenStory does not currently offer custom keybindings, nor `F2` rename or `Delete` file-removal shortcuts in the file tree.

For full details, see [Using the editor](../user-guide/editor.md) and [Collaborating with the AI assistant](../user-guide/ai-assistant.md).

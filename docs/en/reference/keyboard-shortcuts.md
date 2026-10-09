# Keyboard Shortcuts Reference

ZenStory provides a comprehensive set of keyboard shortcuts to help you write more efficiently. Mastering these shortcuts will significantly improve your writing workflow.

[Screenshot: Keyboard shortcuts demo - showing user using Cmd+K to open file search, Cmd+S to save file in editor]

## Global Shortcuts

Global shortcuts work anywhere in the application, giving you quick access to frequently used features.

| Shortcut | Mac | Windows/Linux | Description |
|----------|-----|---------------|-------------|
| Ctrl+K | Cmd+K | Ctrl+K | **Quick File Search** - Search for files in the current project with fuzzy matching and keyboard navigation |
| Escape | Esc | Esc | **Cancel/Close** - Close dialogs, search boxes, or cancel the current operation |

**Tips**:
- After opening search with `Ctrl+K` (Mac: `Cmd+K`), use arrow keys to navigate and press Enter to open a file
- Search supports fuzzy matching on file titles - no need to type the complete filename

[Screenshot: File search interface - showing search box, file list, and keyboard navigation highlight]

## Editor Shortcuts

Editor shortcuts let you keep your hands on the keyboard while writing, helping you stay focused on content creation.

| Shortcut | Mac | Windows/Linux | Description |
|----------|-----|---------------|-------------|
| Ctrl+S | Cmd+S | Ctrl+S | **Save File** - Immediately save the current file and create a version snapshot |
| Ctrl+Shift+Q | Cmd+Shift+Q | Ctrl+Shift+Q | **Add to Quotes** - Add selected text to the chat panel's quote list |
| Ctrl+Z | Cmd+Z | Ctrl+Z | **Undo** - Undo the last edit operation |
| Ctrl+Y | Cmd+Shift+Z | Ctrl+Y | **Redo** - Redo a previously undone operation |
| Ctrl+Shift+Z | Cmd+Shift+Z | Ctrl+Shift+Z | **Redo** - Alternative shortcut for redo |

**Auto-save**: The editor automatically saves your content 3 seconds after you stop typing, so you don't need to save frequently. However, using `Ctrl+S` creates an immediate save with a version record.

**Quote Feature**: Select any text in the editor and press `Ctrl+Shift+Q` to add it to the chat quote list, making it easy to discuss specific content with the AI.

[Screenshot: Editor interface - showing save button, version history button, and quote toolbar after text selection]

### Diff Review Mode Shortcuts

When the AI assistant modifies a file, the editor enters Diff review mode, allowing you to accept or reject changes individually.

| Shortcut | Mac | Windows/Linux | Description |
|----------|-----|---------------|-------------|
| Shift+Y | Shift+Y | Shift+Y | **Accept All Changes** - Accept all AI-proposed changes at once |
| Shift+N | Shift+N | Shift+N | **Reject All Changes** - Reject all AI-proposed changes at once |
| Enter | Return | Enter | **Complete Review** - Confirm review results and exit Diff mode |
| Escape | Esc | Esc | **Cancel Review** - Reject all changes and exit review mode |

**Review Process**: In Diff mode, you can view changes individually, click "Accept" or "Reject" buttons, or use shortcuts for batch operations.

[Screenshot: Diff review interface - showing change highlights, accept/reject buttons, and review toolbar]

## Chat Panel Shortcuts

Chat panel shortcuts are designed to match mainstream chat applications, making your conversations with the AI more fluid.

| Shortcut | Mac | Windows/Linux | Description |
|----------|-----|---------------|-------------|
| Enter | Return | Enter | **Send Message** - Send the current message to the AI assistant |
| Shift+Enter | Shift+Return | Shift+Enter | **New Line** - Insert a line break for multi-line input |
| Tab | Tab | Tab | **Accept Suggestion** - When the input is empty, press Tab to accept the first smart suggestion |
| / | / | / | **Trigger Skill** - Type "/" to open the skill quick-select menu |
| Escape | Esc | Esc | **Close Menu** - Close the skill selection menu or clear input |

**Smart Suggestions**: When the input box is empty, smart suggestion bubbles appear. Press `Tab` to quickly accept the first suggestion, or click the bubble to select other suggestions.

**Skill Trigger**: Typing "/" opens the skill menu with keyboard navigation support:
- Arrow keys: Navigate through the skill list
- Enter/Tab: Select the highlighted skill
- Escape: Close the menu

[Screenshot: Chat panel - showing input box, smart suggestion bubbles, skill menu, and send button]

## File Tree Shortcuts

The file tree supports basic keyboard operations, allowing you to manage project files without a mouse.

| Shortcut | Mac | Windows/Linux | Description |
|----------|-----|---------------|-------------|
| F2 | F2 | F2 | **Rename** - Rename the selected file or folder (planned feature) |
| Delete | Delete | Delete | **Delete** - Delete the selected file (confirmation dialog will appear) |
| Escape | Esc | Esc | **Cancel Operation** - Cancel an ongoing create or rename operation |

**Notes**:
- Delete operations show a confirmation dialog to prevent accidental deletion
- Deleting a folder will also delete all files within it - please proceed with caution

[Screenshot: File tree interface - showing file context menu, rename input box, and delete confirmation dialog]

## Special Notes for Mac Users

If you're using a Mac, please note the following key correspondences:

- **Cmd (Command)** = Ctrl on Windows/Linux
- **Shift** = Shift
- **Return** = Enter

**Examples**:
- Windows/Linux: `Ctrl+K` -> Mac: `Cmd+K`
- Windows/Linux: `Ctrl+Shift+Q` -> Mac: `Cmd+Shift+Q`

## Custom Shortcuts

ZenStory currently does not support user-customized shortcuts. All shortcuts are fixed to ensure a consistent user experience.

**Planned Features**:
- Shortcut customization interface
- Shortcut conflict detection
- Preset scheme switching (e.g., Vim mode, Emacs mode)

If you have specific shortcut requirements or suggestions, we welcome your feedback through:
- Submit an Issue on GitHub
- Join our user community discussion group

## Shortcut Memory Tips

Here are some memory aids to help you quickly remember these shortcuts:

**Global Operations**:
- **K** = **K**nowledge search - `Ctrl+K` for quick file search
- **S** = **S**ave - `Ctrl+S` to save file
- **Q** = **Q**uote - `Ctrl+Shift+Q` to add quote

**Chat Interactions**:
- **Enter** = Send directly, matching chat app conventions
- **Shift+Enter** = Force new line, requires combination key
- **/** = Skill path, similar to command-line feel

**Review Operations**:
- **Y** = **Y**es - `Shift+Y` to accept all changes
- **N** = **N**o - `Shift+N` to reject all changes

## Frequently Asked Questions

### What if shortcuts aren't working?

1. **Check Input Method**: Some Chinese input methods may intercept shortcuts - try switching to English input
2. **Check Focus**: Ensure the focus is on the correct panel (editor, chat, etc.)
3. **Browser Conflicts**: Some browser shortcuts may conflict with the application - try disabling browser extensions

### How can I view currently available shortcuts?

The application currently doesn't have a shortcut help panel. You can:
- Refer to this documentation
- Check button and menu item tooltips
- View status bar hints at the bottom of the application

### Are there shortcuts on mobile?

Mobile devices (phones, tablets) primarily use touch interactions and don't support keyboard shortcuts. If you use a tablet with an external keyboard, some shortcuts may work, but the experience may not be as refined as on desktop.

---

Once you master these shortcuts, your writing efficiency will significantly improve. We recommend gradually familiarizing yourself with them during actual use - there's no need to memorize them all at once. The three most recommended shortcuts to prioritize are `Ctrl+K` (search), `Ctrl+S` (save), and `Enter` (send message).

If you have any questions or suggestions, please contact us through the in-app help center or community channels. Happy writing!

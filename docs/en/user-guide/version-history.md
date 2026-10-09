# Version History

ZenStory's version history feature provides a complete time machine for your creative work. Whether you want to review modification records, compare content differences between versions, or roll back to a previous version, version history makes it all easy.

## What is Version History?

Version history is an automatic modification tracking system that ZenStory creates for each of your files. Every time you save a file, the system automatically creates a new version, completely recording your creative journey.

### Automatic Version Management

You don't need to do anything manually—the system automatically creates versions for the following situations:

- **Create** - When creating a new file
- **Edit** - When manually editing and saving
- **AI Edit** - When AI modifies file content
- **Rollback** - When restoring to a historical version
- **Auto-save** - When the system automatically saves

Each version contains a complete snapshot of the file content, ensuring you can return to any historical point at any time.

---

## Viewing Version History

### Opening the Version History Panel

[Screenshot: Top toolbar, highlighting the "Version History" button location]

To view a file's version history:

1. **Open the target file** - Open the file you want to view in the editor
2. **Click the version history button** - Find the clock icon button in the top toolbar
3. **Browse the version list** - The version history panel will open as a modal

The version history panel displays all historical versions of the current file, arranged in reverse chronological order (newest at the top).

### Version List Details

[Screenshot: Complete version history panel interface, highlighting various elements of the version list]

Each version entry contains the following information:

**Version Identifier**:
- **Version number** - Incremental numbers like v1, v2, v3, etc.
- **Version type tag** - Shows the type of this modification
  - **Create** - File first created
  - **Edit** - User manual edit
  - **AI Edit** - AI assistant modified content (displays robot icon)
  - **Rollback** - Restored from another version
  - **Auto-save** - System automatic save

**Time and Statistics**:
- **Modification time** - Displays relative time (e.g., "2 hours ago", "3 days ago")
- **Word count** - Word count for that version
- **Line changes** - Shows added and deleted lines (green +X lines, red -X lines)

**Version Description** (optional):
- Some versions automatically generate brief modification notes
- Examples: "Expanded the third paragraph of dialogue", "Corrected character name"

**Latest Version Badge**:
- The topmost version displays a "Latest" tag, indicating the currently edited version

---

## Version Comparison Feature

The version comparison feature lets you clearly see specific differences between two versions, perfect for tracking content changes and reviewing modifications.

### Selecting Versions to Compare

[Screenshot: Version list, highlighting selected version entries]

Comparison steps:

1. **Click to select the first version** - Click on a version entry to select it (highlighted)
2. **Click to select a second version** - The system allows selecting up to two versions simultaneously
3. **Click the "Compare" button** - A "Compare" button appears in the toolbar

**Selection Tips**:
- Selected versions display a blue border and background
- When selecting a third version, the earliest selected one is automatically replaced
- The top displays an "X versions selected" prompt

### Viewing Comparison Results

[Screenshot: Version comparison interface, highlighting added and deleted content]

After clicking "Compare", the comparison view expands on the right:

**Comparison View Description**:

- **Version range** - Top displays "Version X → Version Y"
- **Added content** - Displayed with green background, indicating new text
- **Deleted content** - Displayed with red background, indicating removed text
- **Unchanged content** - Displayed normally as context reference

**Closing Comparison**:
- Click the close button (X icon) in the top right corner of the comparison view
- You can continue selecting other versions to compare

---

## Rolling Back to Historical Versions

The rollback feature lets you undo changes and restore to any previous version. This is a major safety net in ZenStory, giving you peace of mind while creating.

### Performing a Rollback

[Screenshot: Rollback button on a version entry (rotating arrow icon)]

Rollback steps:

1. **Find the target version** - Locate the version you want to restore in the version list
2. **Click the rollback button** - Each historical version has a rotating arrow icon on the right
3. **Confirm rollback** - A confirmation dialog appears, displaying:
   > "Are you sure you want to rollback to version X? Current content will be saved as a new version."
4. **Complete rollback** - After confirmation, the file content restores to the selected version

**Important Notes**:

- The latest version (first item) has no rollback button because it is the current version
- Rollback does not overwrite or lose any history
- The rollback operation itself also creates a new version (type: "Rollback")

### Rollback Safety Mechanism

[Screenshot: Version list after rollback, showing the new "Rollback" version]

ZenStory's rollback is a **non-destructive operation**:

- **Current state auto-saved** - Before rollback, the system saves current content as a new version
- **History fully preserved** - All historical versions remain, nothing deleted or overwritten
- **Can rollback again anytime** - You can rollback from a "rollback version" to other versions

**Example Scenario**:

Assume your version history is as follows:
- v5 (Latest) - AI expanded Chapter 3
- v4 - Modified character dialogue
- v3 - Added scene description
- v2 - Created file

If you rollback from v5 to v3, the version history becomes:
- v6 (Latest, rollback version) - Restored to v3 content
- v5 - AI expanded Chapter 3
- v4 - Modified character dialogue
- v3 - Added scene description
- v2 - Created file

You can rollback from v6 to v5 or v4 at any time without losing any content.

---

## Viewing Version Content

Besides comparing and rolling back, you can also view the complete content of any historical version.

### Viewing Historical Version Content

[Screenshot: "View Content" button on a version entry (document icon)]

Operation steps:

1. **Find the target version** - Locate the version you want to view in the version list
2. **Click the document icon** - The document icon button on the right side of the version entry
3. **Browse content** - The complete version content will be displayed

**Use Cases**:
- Want to recall a previously deleted paragraph
- Confirm specific content of a version before deciding to rollback
- Search for a sentence or setting you once wrote

---

## Version History Best Practices

### When to View Version History

**Daily Use**:
- Recall previous creative ideas
- Find deleted content fragments
- Compare writing styles across different versions

**When Collaborating with AI**:
- Review AI modifications (AI edit versions are specially marked)
- If unsatisfied with AI changes, rollback to the pre-modification version
- Compare differences before and after AI modifications to learn and improve writing

**Creative Exploration**:
- Try different writing directions while keeping all exploration records
- Create "safety points" before major changes (although auto-save records everything, you can add descriptions as markers)
- Extract inspiration from old versions to incorporate into current version

### Version Management Tips

1. **Use version comparison wisely** - Before accepting major AI changes, compare to see specific differences

2. **Create with confidence** - Version history is your safety net, don't worry about making mistakes

3. **Understand version types** - Pay attention to version tags to distinguish between your manual edits and AI modifications

4. **Rollback is not the end** - After rolling back, you can continue editing or rollback to other versions again

---

## FAQ

### Q: Is there a limit on the number of versions?

A: The system saves all versions with no hard limit. Versions use differential storage technology and won't take up excessive space.

### Q: Can I delete a specific version?

A: Currently, deleting individual versions is not supported. Version history is a complete timeline; maintaining integrity helps trace the creative process.

### Q: Can I undo a rollback?

A: Yes! The pre-rollback version (v5) is still saved in the history, and you can rollback to it again at any time.

### Q: How many versions can be compared simultaneously?

A: Currently, you can select two versions for comparison. When selecting, they are automatically sorted by version number (older version → newer version).

### Q: Will there be too many auto-saved versions?

A: The system intelligently manages auto-save frequency to avoid creating too many redundant versions. You can also choose whether to display auto-saved versions in version history.

### Q: What's special about AI edit versions?

A: AI edit versions display a robot icon and "AI Edit" tag, making it easy to identify which are AI modifications. Particularly useful when reviewing AI work.

### Q: Can I add custom descriptions to versions?

A: Some interfaces support editing version descriptions. Adding descriptions to important versions can help you quickly identify and locate them later.

---

## Keyboard Shortcuts

The version history panel supports the following shortcuts:

- **Esc** - Close the version history panel
- **Click version entry** - Select/deselect version

---

## Next Steps

Now that you've mastered the version history feature, you can:

- [Learn about file management](./file-tree.md) - Learn how to organize project files
- [Chat with AI](./ai-assistant.md) - Explore how AI helps you modify content
- [Interface overview](./interface-overview.md) - Review the complete ZenStory interface

Happy creating, with no worries!

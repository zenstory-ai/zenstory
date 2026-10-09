# Export Feature

ZenStory supports one-click export of your creative work to local files, making it easy to backup, submit, or share.

## Export Entry

[Screenshot: Top toolbar export button location]

In the project editing page's top toolbar on the right, click the **download icon** button to start the export feature.

- **Desktop**: The export button is located on the right side of the toolbar, displayed as a download icon
- **Mobile**: Click the menu button (three horizontal lines) in the top right corner, then select "Export Content" from the expanded menu

## Export Format

### Plain Text Format (.txt)

[Screenshot: Example of exported TXT file]

ZenStory currently supports exporting to **plain text format (.txt)**, which is the most universal file format with the following features:

- **Wide compatibility**: Almost all text editors and word processors can open it
- **Compact size**: Small file size, easy to transfer and store
- **UTF-8 encoding**: Perfect support for Chinese, preserving all characters and punctuation
- **Windows compatible**: UTF-8 BOM marker added to ensure Windows Notepad displays Chinese correctly

**Suitable Scenarios**:
- Copy and paste to other platforms (Qidian, Jinjiang, and other novel websites)
- Send via email or WeChat
- Quick backup and archiving
- Import into other writing tools

## Export Content

### Chapter Merging

The export feature automatically merges all **Draft** type files in the project:

[Screenshot: Draft folder in file tree]

- Only exports content from the "Drafts" folder
- Outlines, characters, lore, and other files are not included in the export
- Automatically arranged in chapter order
- Chapters separated by a divider (`---`)

### Smart Sorting

[Screenshot: Example of chapter order in exported file]

When exporting, the system intelligently identifies chapter order:

- **Chinese numerals**: Chapter 1, Chapter 2, Chapter 10...
- **Arabic numerals**: Chapter 1, Chapter 2, Chapter 10...
- **Custom order**: Arranged according to the order set in the file tree
- **Creation time**: When same number, sorted by creation time

### Format Preservation

The exported text file preserves:

- Chapter titles (e.g., "Chapter 1: The Beginning")
- Paragraph line breaks in the main content
- Chinese punctuation marks
- Special characters (such as circled numbers, spaces, etc.)

**Note**: Images, bold, italic, and other rich text formatting is not exported; only plain text content is retained.

## Export Process

### Operation Steps

1. **Open Project**
   Enter the project editing page you want to export

2. **Click Export**
   Click the download icon button in the top toolbar

3. **Wait for Download**
   The system automatically generates the file and triggers browser download (usually completes within 1-3 seconds)

4. **View File**
   Find the exported file in the browser download directory. Current default filename format is: `{Project Name}_正文.txt`

### Example

Assume your project is named "My Novel" and contains three chapters:

```
Chapter 1: The Beginning
  Content: This is a story about...

---

Chapter 2: The Journey
  Content: The next morning...

---

Chapter 3: The Ending
  Content: Finally, the protagonist...
```

The exported filename will be: `My Novel_正文.txt`

## FAQ

### Q: No response after clicking the export button?

**Possible Causes**:
- No draft files created in the project yet
- Network connection issue

**Solutions**:
- First create at least one chapter under the "Drafts" folder
- Check network connection, refresh the page and try again

### Q: The exported file has garbled characters?

ZenStory exported files use UTF-8 encoding with BOM marker added, so garbled characters should not occur normally.

**Solutions**:
- Open with Notepad: Windows systems recommend using Notepad or Notepad++
- Open with Word: Word automatically recognizes UTF-8 encoding
- Mac system: Open with TextEdit or VS Code

### Q: Chapter order is incorrect after export?

Chapter sorting is based on the following rules:

1. Order set in the file tree (order field)
2. Numbers in chapter titles (Chapter 1, Chapter 2, etc.)
3. File creation time

**Adjustment Methods**:
- Drag and drop to adjust chapter positions in the file tree
- Modify chapter titles using standard naming (e.g., "Chapter 1", "Chapter 2")

### Q: Can I export only some chapters?

Currently, the export feature merges all draft files. To export partial chapters:

**Temporary Workaround**:
1. Export the entire project
2. Open with a text editor, manually delete unwanted chapters
3. Save the modified file

### Q: Is exporting to Word or PDF format supported?

The current version only supports plain text (.txt) format export. Word, PDF, and other format support will be available in future versions.

**Alternatives**:
- Use local office tools to convert TXT into your required document format
- Use a trusted converter to turn TXT into PDF (check privacy settings)

## Export Tips

### Submission Preparation

Preparing for novel website submissions:

1. Export TXT file
2. Open with Notepad, check formatting
3. Copy all content
4. Paste into the submission platform's editor

### Batch Backup

Regularly backup your creations:

1. Export once after completing each important chapter
2. Filename automatically includes project name
3. Save exported files to cloud storage (such as Baidu Netdisk, iCloud)

### Multi-version Management

Keep different versions of manuscripts:

1. Rename the file after export, add date suffix
   - `My Novel_正文_20240115.txt`
   - `My Novel_正文_20240220.txt`
2. Easy to compare creations from different periods

### Offline Writing

Continue creating in an environment without internet:

1. Export current manuscript
2. Modify in a local editor
3. When you have internet, copy the modified content back to ZenStory

## Technical Details

### File Encoding

- **Encoding format**: UTF-8 with BOM
- **Line endings**: Automatically adapted to operating system
- **Compatibility**: Perfect support for Windows, Mac, Linux

### Chapter Separator

Standard separator between chapters:

```
---
```

This is three consecutive hyphens, making it easy to identify chapter boundaries.

### File Naming Convention

Export filename format (current default): `{Project Name}_正文.txt`

- Supports Chinese characters
- Automatically handles special characters
- Complies with operating system filename conventions

### Performance Notes

- **Small projects** (<10 chapters): 1-2 seconds
- **Medium projects** (10-50 chapters): 3-5 seconds
- **Large projects** (50+ chapters): May take more than 10 seconds

Do not close the page during export; wait for the browser to complete the download.

---

> **Tip**: It's recommended to develop a habit of regular export backups to ensure your creative work is always safe.

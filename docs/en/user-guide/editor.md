# Editor Guide

The editor is the core creative space of ZenStory, providing you with a smooth, intelligent writing experience. This document will introduce the various features and usage techniques of the editor in detail.

---

## Editor Interface

[Screenshot: Complete editor interface, annotated with title area, toolbar, content area, status bar]

The editor adopts a clean and intuitive design, mainly containing the following areas:

### Title Area

[Screenshot: Title input box at the top of the editor]

- **File Title**: Displays the name of the currently edited file
- **Editable**: Click to directly modify the title
- **Auto-sync**: Title modifications automatically update in the file tree

### Content Area

[Screenshot: Central content editing area of the editor]

- **Spacious editing space**: Comfortable line height and font size
- **Real-time content display**: WYSIWYG editing experience
- **Streaming output support**: Real-time display when AI generates content

### Status Bar

[Screenshot: Status bar at the bottom of the editor]

The bottom status bar provides important real-time information:
- **Word count**: Displays the current content's word count (excluding spaces)
- **Paragraph count**: Statistics of the number of paragraphs in the content
- **Save status**: Displays the current file's save status
- **History**: Quick access to version history

---

## Basic Editing Operations

### Input and Editing

[Screenshot: Editing in progress, showing cursor and input content]

**Start Editing**:
1. Click on any file in the left file tree
2. The editor automatically loads the file content
3. Click on the content area to start editing

**Editing Features**:
- **Smooth input**: Optimized text input experience, supports IME input methods
- **Auto height**: The editor automatically adjusts height based on content
- **Distraction-free**: Clean interface design lets you focus on the content itself

### Real-time Saving

[Screenshot: Save status indicator, showing "Saved", save time, etc.]

**Auto-save Mechanism**:
- **Smart save**: Content is automatically saved 3 seconds after modification
- **Manual save**: Press `Cmd/Ctrl + S` or click the "Save" button
- **Status indicators**:
  - "Saving..." - Save in progress
  - "Just saved" - Save successful
  - "X seconds/minutes ago" - Shows last save time
  - "Unsaved" - There are unsaved changes

**Save Reliability**:
- All changes automatically create version records
- Local cache protects your content during network interruptions
- Supports version rollback (see [Version History](./version-history.md))

### Undo and Redo

Use standard keyboard shortcuts to manage your editing history:
- **Undo**: `Cmd/Ctrl + Z`
- **Redo**: `Cmd/Ctrl + Y` or `Cmd/Ctrl + Shift + Z`

---

## Word Count Statistics

[Screenshot: Word count position in the status bar]

The editor provides accurate real-time word and paragraph statistics:

### Word Count Calculation

- **Counting method**: Actual word count after removing all whitespace characters
- **Real-time update**: Updates immediately after each input
- **Accurate and reliable**: Precise counting of Chinese characters, English words, and punctuation

### Paragraph Statistics

- **Paragraph definition**: Text blocks separated by blank lines
- **Creative reference**: Helps you control chapter length and pacing

### Usage Scenarios

- Monitor daily writing goals
- Control chapter word count
- Meet submission word count requirements

---

## AI-Assisted Editing

### Add Reference to Chat

[Screenshot: Floating toolbar appearing after selecting text]

The editor is deeply integrated with the AI assistant, allowing you to easily reference editor content in AI conversations:

**How to Use**:
1. Select any text in the editor
2. Wait 0.3 seconds, and a floating toolbar will appear above the text
3. Click the "Add Reference" button (quote icon)
4. The selected text will be automatically added to the AI chat's reference list

**Keyboard Shortcut**:
- After selecting a file, press `Cmd/Ctrl + Shift + Q` to quickly add a reference

**Reference Uses**:
- Have AI continue writing based on selected content
- Ask AI questions about selected paragraphs
- Request AI to polish or rewrite selected content

### AI Streaming Output

[Screenshot: Editor during AI writing, with "AI is writing..." animation displayed at the top]

When AI generates content for you:

**Real-time Display**:
- Content displays character by character, no waiting required
- "AI is writing..." animation indicator at the top
- Automatically scrolls to the latest content

**Smart Scrolling**:
- If you are viewing the bottom, the editor follows new content automatically
- If you scroll up to view historical content, the editor won't interrupt you
- Scroll to the bottom to resume auto-following

**Content Saving**:
- Automatically saved after AI generation completes
- Automatically creates version record, marked as "AI Edit"

### AI Diff Review Mode

[Screenshot: Diff review mode interface, showing modification comparison and accept/reject buttons]

When AI edits existing files, it enters diff review mode:

**Review Interface**:
- Review toolbar displayed at the top
- Shows total modification count and pending review count
- Each modification is highlighted in different colors:
  - Green: Added content
  - Red: Deleted content
  - Yellow: Pending review status

**Review Actions**:
1. **Review individually**: Click on each modification, choose "Accept" or "Reject"
2. **Batch operations**:
   - Click "Accept All" to accept all modifications
   - Click "Reject All" to reject all modifications
3. **Keyboard shortcuts**:
   - `Shift + Y`: Accept all modifications
   - `Shift + N`: Reject all modifications
   - `Enter`: Complete review
   - `Escape`: Cancel review (reject all)

**Complete Review**:
- After reviewing, click "Apply Changes" or "Complete Review"
- System automatically saves the final content
- Creates version record, marked as "AI Edit (Reviewed)"

---

## Version History

[Screenshot: Version history panel]

The editor creates version snapshots for every important modification:

### View History

1. Click the "History" button in the status bar
2. Browse the list of all historical versions
3. Click on any version to view detailed content

### Version Information

Each version record contains:
- **Modification time**: Precise to the second
- **Modification type**: User edit, AI edit, AI edit (reviewed)
- **Modification summary**: Brief description of the changes
- **Content preview**: Quick browse of version differences

### Rollback Operation

- Select any historical version
- Click "Rollback to this version"
- System restores to that version's content

For detailed features, please refer to the [Version History](./version-history.md) document.

---

## File Type Descriptions

The editor supports multiple file types, each with its specific purpose:

### Outline Files

- **Purpose**: Plan story structure, chapter arrangements
- **Features**: Supports hierarchical structure
- **Suggestion**: Use clear headings and indentation to organize content

### Draft Files

- **Purpose**: Write actual novel content
- **Features**: Supports long-form editing, auto-save
- **Suggestion**: Create separate draft files by chapter or scene

### Character Files

- **Purpose**: Record character settings, personality traits
- **Features**: Structured information display
- **Suggestion**: Create separate files for each main character

### Lore Files

- **Purpose**: Build world-building, background settings
- **Features**: Categorized management of different setting elements
- **Suggestion**: Organize lore content by theme

---

## Editing Tips

### Efficient Writing

1. **Leverage auto-save**: Focus on content without frequent manual saves
2. **Use version history**: Experiment boldly, you can always rollback
3. **Reference content to AI**: Select excellent passages to let AI learn your style

### Collaborating with AI

1. **Step-by-step generation**: Don't ask AI to generate complete chapters at once; generating by scene works better
2. **Detailed instructions**: Give AI enough context and clear requirements
3. **Review modifications**: Carefully review each AI modification to maintain your creative style

### Content Management

1. **Regular organization**: Use folders to organize related files
2. **Timely backup**: Regularly export projects (see [Export Feature](./export.md))
3. **Version marking**: Create version notes before important modifications

---

## Mobile Editing

[Screenshot: Mobile editing interface]

ZenStory's editor fully supports mobile devices:

### Touch Operations

- **Tap**: Position cursor
- **Long press**: Select text
- **Double tap**: Select word
- **Triple tap**: Select paragraph

### Virtual Keyboard Adaptation

- Editor automatically adjusts to accommodate keyboard appearance
- Keeps editing content visible
- Supports keyboard toolbar operations

### Mobile Features

- **Bottom navigation**: Quick switch between files, editing, AI assistant
- **Gesture back**: Supports system back gesture
- **Orientation adaptation**: Auto-adjusts layout

### Mobile Recommendations

- Suitable for short-term creation and quick modifications
- Large-scale writing recommended on desktop
- Use AI voice input feature to improve efficiency

---

## FAQ

### Editor not responding?

1. Check network connection status
2. Refresh the page (content has been auto-saved)
3. Clear browser cache and try again

### Content lost?

1. Click the "History" button to view version history
2. Select the most recent version to rollback
3. Contact technical support for assistance

### AI-generated content doesn't meet expectations?

1. Provide more detailed context in the conversation
2. Use the reference feature to let AI understand your style
3. Try modifying instructions, generate content step by step
4. Reject inappropriate modifications during review

### Word count inaccurate?

Word count removes all whitespace characters. If your copied content contains many spaces or line breaks, the word count will be less than expected. This is a normal counting method.

---

## Keyboard Shortcut Summary

| Shortcut | Function |
|----------|----------|
| `Cmd/Ctrl + S` | Manual save |
| `Cmd/Ctrl + Z` | Undo |
| `Cmd/Ctrl + Y` | Redo |
| `Cmd/Ctrl + Shift + Z` | Redo (alternative) |
| `Cmd/Ctrl + Shift + Q` | Add selected content to AI chat reference |
| `Shift + Y` | Accept all AI modifications (review mode) |
| `Shift + N` | Reject all AI modifications (review mode) |
| `Enter` | Complete review (review mode) |
| `Escape` | Cancel review (review mode) |

---

## Next Steps

Now that you've mastered the editor's usage, you can continue exploring:

- [AI Assistant](./ai-assistant.md) - Learn how to collaborate efficiently with AI
- [Version History](./version-history.md) - Understand version management features
- [File Management](./file-tree.md) - Master project file organization techniques

Start your creative journey!

# AI Creative Assistant

## What is the AI Creative Assistant?

The AI Creative Assistant is ZenStory's core feature. It acts like a writing partner who understands you. It can comprehend your story background, character settings, and writing style, providing creative inspiration, content generation, and intelligent suggestions. Whether you're constructing outlines, enriching characters, or writing drafts, the AI serves as your capable assistant.

Unlike traditional writing tools, the AI Creative Assistant actively understands your project context. When you open an outline or draft, the AI automatically retrieves relevant content; when you select a passage of text, the AI can continue or modify based on that text. This deep understanding makes the creative process more fluid and natural.

---

## Starting a Conversation

### Entering Creative Requests

[Screenshot: Chat input box at the bottom right of the interface]

In the input box at the bottom of the chat panel on the right, enter what you want the AI to help you with.

**Example inputs:**
- "Help me design a dramatic backstory for the female protagonist"
- "Based on the current outline, continue writing the beginning of Chapter 3"
- "Optimize this dialogue to make the character's personality more distinct"

The input box supports multi-line text, so you can describe your needs in detail. Press `Enter` to send a message, press `Shift + Enter` for a new line.

### Using Suggestion Prompts

[Screenshot: Suggestion bubbles above the input box]

If you don't know where to start, you can click the suggestion prompts above the input box. These prompts are dynamically generated based on your project status, helping you quickly initiate conversations.

**Prompt types:**
- **New Project Guidance** - When a project is just created, provides suggestions for creating outlines, characters, and other basic content
- **AI Smart Suggestions** - The AI generates relevant follow-up suggestions based on your recent conversation content

**How to use:**
- Click any suggestion bubble to automatically fill the content into the input box
- Click the refresh button to get new suggestions
- Press `Tab` to quickly adopt the first suggestion

### Voice Input

[Screenshot: Microphone button on the left side of the input box]

If you prefer to dictate your thoughts, you can use the voice input feature.

**Desktop operation:**
1. Click the microphone button to start recording
2. Speak into the microphone (maximum 55 seconds)
3. Click the button again to stop recording, and the AI will automatically recognize and convert to text

**Mobile operation:**
1. Long press the microphone button to start recording
2. Keep holding and speak
3. Release the button to stop recording and recognize

**Notes:**
- Requires browser microphone permission
- Right-click (or long press and slide away) to cancel recording
- Recognition results will be appended to existing content in the input box

---

## AI Responses

### Thinking Process

[Screenshot: Collapsible thinking process panel with expand/collapse arrow]

When the AI processes complex tasks, it first displays the thinking process. This content is shown in a semi-transparent style, expanded by default, and you can click the title bar to collapse it.

**The thinking process includes:**
- AI's understanding of the task
- Planned steps to take
- Context factors considered

The thinking process helps you understand the AI's reasoning logic and makes the creative process more transparent.

### Creative Results

[Screenshot: Main content area of AI output]

The AI's main response is displayed in a clear format, supporting:
- **Markdown formatting** - Headers, lists, bold, etc.
- **Code blocks** - If format examples are involved
- **Real-time streaming output** - You can see the AI generate content character by character

### Tool Operations

[Screenshot: Tool result card showing file operation status and details]

When the AI performs specific operations (such as creating files, editing content), tool result cards are displayed:

**Creating files:**
```
✓ Created Outline "Chapter 1"
```

**Editing files:**
```
✓ Edit Complete "Chapter 3"
[View Edit Details ▼]
```

Click "View Edit Details" to see:
- Deleted content (red background)
- Added content (green background)
- Operation type (replace/insert/append/delete)

**Querying files:**
```
✓ Found 3 related files
- Character "Li Ming"
- Lore "Worldbuilding - Cultivation System"
- Outline "Volume 1"
```

---

## Collaborating with AI

### Confirmation and Rejection

Some AI operations may require your confirmation. When the AI is uncertain how to proceed, it will pause and ask for your input:

[Screenshot: Workflow waiting for confirmation status prompt]

```
⚠ Waiting for Your Response
AI needs more confirmation to continue
```

At this point you can:
- Provide more details for the AI to continue
- Modify previous instructions
- Send "continue" to let the AI proceed at its discretion

### Undoing Operations

If the AI edited a file but you're not satisfied, you can undo the modification:

[Screenshot: Undo button in the edit result card]

1. Find the "Undo" button in the tool result card
2. After clicking, the AI's modification will be rolled back to the pre-edit version
3. After undoing, you can ask the AI to regenerate

**Note:** The undo function only works for recent edits. If new edits have been made since, previous edits may not be undoable.

### Multi-turn Conversations

You can have multi-turn conversations with the AI to gradually refine content:

**Example flow:**
```
You: Help me design an antagonist character
AI: [Generates character profile]
You: Make them more complex, give them a tragic childhood
AI: [Modifies and adds background]
You: Now based on this character, write their entrance scene
AI: [Generates narrative excerpt]
```

The AI remembers conversation history and understands contextual relationships. You can follow up, modify, or request regeneration at any time.

---

## Context Management

The AI automatically retrieves and uses project context information, but you can also actively manage this context.

### Current Editing File

When you open a file (outline, character, lore, draft), the AI automatically sets it as the "focus file":

[Screenshot: File opened in editor showing file title]

- **Auto-transmission:** When you send a message, the AI receives the current file's content
- **Context understanding:** The AI can continue, modify, or expand based on the current file

**Use cases:**
- Open an outline → Let the AI help you refine chapter structure
- Open a character card → Let the AI expand the character's personality
- Open a draft → Let the AI continue or optimize the content

### Referencing Materials

In addition to the current file, you can attach other materials as references:

[Screenshot: "Attach to Conversation" option in the file tree context menu]

**How to use:**
1. Right-click a file in the left file tree
2. Select "Attach to Conversation"
3. The file will appear in the attachments area above the input box

**Maximum of 5 files can be attached**, and the AI will reference this content when processing.

**Suitable scenarios:**
- Reference multiple character profiles when writing ensemble scenes
- Create based on worldbuilding settings
- Maintain consistent style across chapters

### Referencing Text

If you want the AI to handle specific text, you can quote it:

[Screenshot: "Quote" button appearing after selecting text in the editor]

**How to use:**
1. Select a passage of text in the editor
2. Click the "Quote" button (or use keyboard shortcut)
3. The selected text will appear above the input box

**Maximum of 5 text passages can be quoted**, each recording the source file.

**Use cases:**
- "Optimize the pacing of this dialogue"
- "Make this description more vivid"
- "Expand based on this setting"

---

## Starting a New Session

[Screenshot: "+" button in the chat panel title bar]

If you want to start a completely new conversation, you can create a new session:

1. Click the "+" button in the chat panel title bar
2. The current session's message history will be cleared
3. The AI will start a new conversation context

**When to start a new session:**
- Switching to a completely different creative task
- Previous conversation content is interfering with new requests
- Wanting the AI to "forget" previous discussions

**Note:** Starting a new session does not delete history. You can still find previous sessions in the conversation history.

---

## Conversation History

The AI saves your conversation history, automatically loading the most recent 50 messages each time you open a project.

**History features:**
- Cross-device sync (log in to the same account)
- Includes complete tool calls and results
- Supports scrolling up to view earlier messages

**To view complete history:**
- All messages are saved in the database
- Can query complete records through the backend API

---

## Usage Tips

### 1. Be Specific in Your Descriptions

Vague instructions make it difficult for the AI to understand your intent.

**Poor example:**
> "Write a fight scene"

**Better example:**
> "Write a duel between two swordsmen on a rainy night, emphasizing the protagonist's disadvantage and decisive counterattack, with a cold and sharp style"

**Specific descriptions include:**
- Who are the characters
- What is the setting
- What happens
- What style do you want
- What effect do you need

### 2. Provide Sufficient Background Information

Although the AI automatically retrieves context, actively providing key information works better:

**Example:**
> "Based on the antagonist character just designed, combined with the 'demon cultivator' setting in the worldbuilding, write their first entrance scene. Make the reader feel threatened while maintaining some mystery."

**Key information:**
- Explicitly referenced the previous character design
- Pointed out specific settings in the worldbuilding
- Explained the desired narrative effect

### 3. Make Good Use of the Quote Feature

The quote feature lets the AI know exactly what content you want to handle:

**Scenario examples:**
- Select a dialogue in a draft → Quote → "Make this dialogue more consistent with the character's sarcastic tone"
- Select a rule in the lore → Quote → "Based on this setting, design a related plot conflict"

Quoting is more accurate than verbal description; the AI can see the original text directly.

### 4. Complete Complex Tasks Step by Step

For complex creative tasks, breaking them into steps works better:

**Example:**
```
Step 1: Help me design a cultivation world's ranking system
Step 2: Based on this system, create the protagonist's cultivation progress table
Step 3: Now based on this progress table, plan the outline for the first three volumes
Step 4: Write detailed chapter outlines for Volume 1
```

Benefits of this approach:
- The AI can focus on the current task in each step
- You can adjust direction in time
- Final results better match expectations

### 5. Leverage AI Memory

The AI remembers key project information (can be viewed and edited in "AI Memory"):

[Screenshot: Database icon button in title bar, clicking shows project status dialog]

**AI Memory includes:**
- Project summary
- Current creative stage
- Writing style preferences
- Important notes

**How to leverage:**
- Regularly update project summary to keep the AI informed of overall progress
- Record special requirements in notes
- The AI provides more tailored suggestions based on memory

### 6. Combine Multiple Interaction Methods

The most efficient workflow typically combines multiple methods:

**Recommended flow:**
1. Open the file to edit (auto-transmit context)
2. Attach relevant setting files (actively reference materials)
3. Select the paragraph to optimize (precise text quoting)
4. Enter specific modification requirements (clear instructions)

This gives the AI the most complete context and provides the most precise assistance.

---

## Frequently Asked Questions

### Q: Will the AI automatically save my content?

A: When the AI creates or edits files, they are automatically saved to the database. A version snapshot is created after each important modification, and you can roll back to previous versions at any time.

### Q: Why does the AI sometimes "forget" what was said before?

A: The AI's context window has a limit. If a conversation is very long, earlier content may not be in the current context. Solutions:
- Use the "quote" feature to re-provide key information
- Start a new session and re-clarify the current task

### Q: What if the AI's response is interrupted?

A: Possible reasons:
- Network interruption: Refresh the page and retry
- Content too long: Ask the AI to generate in multiple parts
- System timeout: Try again later

You can send "continue" to let the AI continue from where it was interrupted.

### Q: How can I make the AI better understand my style?

A:
- Specify writing style in project settings
- Let the AI analyze excerpts of your existing work
- Clearly state style requirements in instructions (e.g., "in the style of Hemingway")

### Q: Can the AI directly modify files without my confirmation?

A: Yes, the AI can directly edit files by default. But each edit:
- Creates a version snapshot with one-click undo support
- Displays detailed edit differences
- You can adjust to "require confirmation" mode in settings

---

The AI Creative Assistant is your writing partner who truly understands you. Master these usage tips to make your creative work twice as effective. Start your first conversation now!

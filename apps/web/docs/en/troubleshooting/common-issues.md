# Troubleshooting common issues

Whenever something goes wrong in the editor, **copy any unsaved prose to a safe local file first** before refreshing, signing out, or clearing browser data. Blindly reloading the page, wiping site storage, or re-sending the same long prompt rarely fixes the root cause and can discard local drafts.

## Unsaved edits or save conflicts

1. Copy unsaved text from the editor into a local note or document right away.
2. Check your network connection and the status indicator in the editor status bar, then press `Command/Ctrl + S` to retry saving.
3. If a **save conflict** dialog appears, compare the newer cloud version against your local draft, merge the lines you want to keep, and save.
4. If the editor offers a **local draft recovery** banner when you reopen a file, inspect both versions carefully before choosing whether to restore the local copy or keep the cloud text.

If the browser warns that local draft caching failed (for example, in restricted private browsing or full local storage), save a copy outside the browser before closing or refreshing the tab. See [Using the editor](../user-guide/editor.md).

## AI reply is slow, interrupted, or stopped early

Before retrying, check the chat progress cards and open the target file to see what the AI has already read or written. Multi-file revisions and full-chapter drafts naturally take longer than short chat answers—reloading after 30 seconds will only interrupt an active stream.

If a turn drops mid-way or you clicked **Stop**, read the current end of the file first and ask the AI to continue **only the missing remainder** so it does not write duplicate chapters. If chat reports a daily limit, permission, or service error, resolve that message first—see [Error messages and next steps](error-messages.md).

## Sign-in or verification code issues

- Double-check your email address or username and make sure Caps Lock is not changing your password case.
- If a verification email has not arrived, check Spam / Promotions, confirm the email spelling, and wait for the resend timer before requesting a new code.
- Forgot your password—or signed up with Google and want password login too? Click **Forgot password?** on the sign-in screen to set one via email verification.
- If Google sign-in returns an authorization error, note the message and start the sign-in flow again from the login page; contact support if it repeats.

Copy any unsaved editor text before signing out and back in. You do not need to enable all third-party cookies or wipe browser storage to troubleshoot login. See [Registration and sign-in](../getting-started/installation.md).

## Missing files, missing projects, or import hiccups

First confirm you are signed into the right ZenStory account and looking inside the right project, then click **Retry** if a list failed to load. If files appear missing in the sidebar, clear the file-tree search box and any active filters. When creating a project or custom skill fails, check the exact prompt for name-length rules or plan limits.

If a multi-file manuscript import partially succeeds, inspect which chapters were already created in **Drafts** before re-importing the failed file so you do not create duplicates. To rename a file, edit its title at the top of the editor—never delete and recreate a file just to change its name.

## Reference novel breakdown fails or shows empty tabs

Check the error reason on the novel card and verify your remaining breakdown allowance. Uploaded novels must be UTF-8 or GBK `.txt` files within size limits and must have clear standalone chapter headings (`Chapter 1`, `第一章`, etc.). If a completed breakdown has an empty category tab, check how many chapters were analyzed—free trials analyze only the **first 3 complete chapters** (up to 15,000 characters), and not every excerpt contains every category (such as special powers).

Pro users with remaining monthly breakdowns should use **Retry** on a failed card instead of uploading duplicate copies. See [Materials Library](../user-guide/materials.md).

## Export fails or downloaded TXT looks garbled

Make sure your chapters are saved to the cloud (`Saved` in the status bar) and sit inside **Drafts** or **Scripts** (`draft` / `script` types). Check whether your browser blocked the file download. Exported `.txt` files use **UTF-8** encoding—if older desktop software displays garbled characters, re-open the file with UTF-8 selected and keep the original export intact before converting encodings.

Outlines, character sheets, and worldbuilding files are excluded from whole-manuscript export; open any individual planning file in the editor and copy it directly to save it locally. See [Export your manuscript](../user-guide/export.md).

## Editor or page feels sluggish

Save or copy your current text first, close heavy background browser tabs, and check whether the slowdown happens only inside one unusually long file. Splitting book-length drafts into individual chapter files keeps editing, diff review, and AI context snappy. If lag continues on normal-sized chapters, send support the approximate word count, browser version, and steps—there is no need to clear your entire browser history.

## Contact support

Reach us through in-app feedback or at [support@zenstory.ai](mailto:support@zenstory.ai) with the exact error text, approximate time, page URL, steps leading up to the problem, and your browser version. Redact private story text and never share passwords, verification codes, or tokens.

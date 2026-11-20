# Error messages and next steps

When ZenStory shows an error banner or dialog, read the specific message before refreshing or retrying—most alerts tell you directly whether to back up local text, sign back in, adjust an upload, or wait.

## Saving, conflicts, and version history

| What the message indicates | Recommended next step |
| --- | --- |
| Cloud file changed elsewhere (save conflict) | Copy your local draft just in case, compare it with the newer cloud text, merge what you want to keep, and save |
| File saved, without adding a new manual history version | Your latest text is safely saved in the cloud; your file's manual version slots (10 on Free / 100 on Pro) are full and could not merge—export milestone `.txt` backups locally or upgrade |
| Browser could not cache the local draft | Copy unsaved text to a local file right away; do not rely on browser refresh recovery in this window |
| Selected history version no longer exists | Reopen **History** to refresh the list and confirm you have the intended file and snapshot open |
| Pre-restore backup could not be created | The restore was safely aborted so your current text stays untouched; copy your current draft locally and resolve the save error before retrying |

See [Using the editor](../user-guide/editor.md) and [Version history](../user-guide/version-history.md).

## Account, login, and plan limits

| What the message indicates | Recommended next step |
| --- | --- |
| Session expired or invalid login state | Copy any unsaved text in the editor first, then sign in again |
| Incorrect email/username or password | Verify your login identifier and password case, or click **Forgot password?** to reset via email |
| Email address not verified yet | Enter the 6-digit verification code sent to your registration email |
| Verification code incorrect or expired | Check the newest email in your inbox (codes expire after 5 minutes for registration, or 10 minutes for password reset) or wait for the countdown to request a fresh code |
| Invite or redemption code invalid, expired, or used up | Double-check every character of the code, or confirm with the sender whether a new code is needed |
| Daily free AI messages used up | Save your work and continue after **00:00 Beijing time (UTC+8)**, or upgrade to Pro for unlimited daily messages |
| Active project or custom-skill limit reached | Check **Plans & Benefits**; delete only unused test items to free a slot, or upgrade to Pro |

Never delete active manuscripts just to free up a project slot—export backups first if you are archiving old work. See [Plans and benefits](../user-guide/billing-benefits.md).

## Files, imports, uploads, and export

| What the message indicates | Recommended next step |
| --- | --- |
| File or project not found | Confirm you are signed into the owning account and right project; check chat cards if the AI recently deleted a file (click **Restore** there) |
| Permission or plan access denied | Sign in with the owning account, or check whether the feature requires Pro |
| Unsupported format, file too large, or character count exceeded | Convert to plain UTF-8 / GBK `.txt` (or `.md` where supported) and split or trim the file to fit that upload dialog's stated limit |
| No chapters recognized during novel upload | Make sure chapter headings (`Chapter 1`, `第一章`, etc.) sit on their own lines, save the `.txt` file, and select it again |
| No exportable manuscript chapters found | Verify that your chapters live under **Drafts** or **Scripts** (`draft` / `script`) and show **Saved** |

Note that the Free 3-chapter trial and Pro full-book breakdowns enforce different size and chapter limits—see [Materials Library](../user-guide/materials.md).

## AI chat, voice input, and payment processing

If AI chat reports a temporary service error, connection drop, or internal failure, inspect the open file before sending the prompt again—any edits completed before the interruption are already saved, so ask only for what remains instead of regenerating the whole chapter from scratch.

If voice input fails, check browser microphone permissions and confirm another app is not holding the mic, or switch to typing. If Pro status does not appear immediately after checkout, give payment confirmation a moment and refresh **Plans & Benefits**; if you were charged and Pro still does not activate, keep your order receipt and contact support without paying a second time.

## Reporting a persistent issue

Copy the exact error text along with the time it happened, the page URL, what you clicked right before it appeared, and your browser version, then send it via in-app feedback or [support@zenstory.ai](mailto:support@zenstory.ai). Once an operation fails consistently with the same error, save that info for support rather than clicking retry in a loop.

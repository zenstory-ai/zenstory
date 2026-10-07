# M05 ordinary late-save completion follow-up

## Grounded defect

Offline real App/Layout/Editor/SimpleEditor browser probes establish two REDs:
dirty/title A is legitimately saved during responsive unmount, replacement B
(same project) or C (another project) is visibly selected, then successful old
A PUT completion selects A again. The write itself is permitted. The defect is
the stale shared selection callback using captured `selectedItem`, not a proven
loss of B/C text or unauthorized backend access.

Use existing live project/selection/loaded-file refs to fence shared selection
completion; retain legitimate final dirty writes, queue-owned concurrency tokens,
operation notifications and appropriate same-project rename tree reconciliation.
Lock both actual unmount cases, mounted navigation, ordinary rename and default/
root StrictMode controls before source edits. Separate boundary advice and source
review are required; no generation/store/dependency redesign is justified.

## Preserve useful conflict recovery

Current browser control: old A final save409 while B is selected leaves B intact;
returning to A exposes its unsaved local draft through existing diff review.
Do not blanket-discard old409 callbacks and silently lose that recovery. Competing
review or cross-project409 behavior has not been proven here and is not a claim
of corruption or authorization for a durable-draft framework.

## Correct the styling prerequisite before layout repairs

The initial reused evidence bundle does not contain `.hidden`, `md:flex`, `w-80`,
`h-12` or `z-50`, although actual Header/ProjectSwitcher source uses them. Its
390px screenshot shows both logos and desktop actions, and a43px menu column.
Thus mobile menu pointer interception and clipped Save geometry are currently
**unclassified until valid CSS/build provenance**, not grounded product layout
REDs. The same native frontend lane is diagnosing scan-root/build/loaded computed
styles and rerunning only affected probes against a valid exact-source local
bundle. No force-click, injected hand CSS, relaxed assertions or product source
edits are authorized by those observations alone.

Evidence: `parallel-native-tmux/frontend/m05-browser-review/REPORT.md` and its
final/geometry receipts; later `css-validation/` supersedes styling claims, not
silently rewriting earlier failed/invalid runs. No fullM05/all23/release approval.

# Writing guidance: one canonical content store

## Runtime boundaries

- `SystemPromptConfig` in the primary database is the sole project-writing configuration for `novel`, `short`, and `screenplay`. Missing/inactive rows raise an explicit service-unavailable error before the writing workflow starts. On an already-open SSE response this is an `error` event, not a change to the HTTP status.
- Admin prompt updates require `expected_version`. After saving, use `/api/admin/prompts/reload`; cache is process-local, so reload every API process or restart deployments. A code deployment alone does not update database prose.
- Official skill instructions/descriptions live in existing approved `PublicSkill` rows. `UserAddedSkill` references these rows; updates do not require users to remove/re-add skills. IDs, names, categories, tags, metadata, approval state and usage/add counters are preserved.
- Shared tool/streaming/role contracts and next-step suggestions remain active code. These are not alternate project-content defaults. User SKILL.md/ZIP parsing, resources, upload and export remain supported.
- Removed: three Python project manuscripts, thirteen filesystem official skill packages, seed loader/service and force-import CLIs. Fresh databases need intentionally administered project configurations; there is no hidden bootstrap catalog. Test fixtures are explicitly synthetic, not production defaults.

## Content-only official maintenance

Use `apps/server/scripts/update_official_skill_content.py` with a separately reviewed JSON array. Each item has only `id`, `name`, `expected_description`, `expected_instructions`, `description`, and `instructions`. Expected values come from the current database snapshot, not from old source files.

```sh
cd apps/server
python scripts/update_official_skill_content.py /private/reviewed-patch.json
python scripts/update_official_skill_content.py /private/reviewed-patch.json \
  --apply --backup /private/new-before-snapshot.json
```

The default is a rollback-only validation. Apply requires an exclusive, mode-0600 backup, validates every item, and compares ID/name/source/status/old text atomically before changing description/instructions only. The complete patch commits in one transaction; stale or non-official rows abort. Restrict database access to operators. Review/read back the changed rows and verify already-added skill loading; never use this tool to reseed identities or community content. Patch artifacts are one-time change records, not another runtime catalog.

English readers see official skills through `apps/server/config/official_skill_translations.py` (keyed by the stored Chinese name). A patch or admin change that adds, renames, unpublishes or rewrites the description of an official skill ships with the matching table update in the same batch.

## Knowhow provenance and deliberate omissions

References are pinned repository sources, not instructions to invoke their CLI pipelines:

| Source revision | Source paths | Adapted mechanism |
| --- | --- | --- |
| drama-skills `4e48ccbf0f77da757d7cacc6937b1cc59c124845` | `skills/short-drama/references/creator-workflow.md` | Author intent, requested artifacts, provisional drafts versus accepted canon |
| same | `skills/short-drama-develop/references/reveal-reversal-payoff.md` | Reveal evidence/access/why-now/new action; reversal invalidates a plan and creates costly choice; local payoff precedes outgoing pressure |
| same | `skills/short-drama-review/references/rubric-story-script.md`, `review-method.md` | Playable action, spatial continuity, evidence/impact/minimal repair/preserve-set review |
| oh-story `a06c3c5ff39209ef8dcba6e8c4ac1d9920e11e32` | `.claude/skills/story-long-write/SKILL.md`, `references/emotional-methods.md`, `style-resolution.md` | Discussion/outline/prose scope; emotional carrier, obstruction, active choice and meaning change; canon/POV before style |
| same | `.claude/skills/story-short-write/SKILL.md`, `.claude/agent-references/quality-checklist.md` | Emotional spine; information/relationship/risk/resource/decision/action/understanding changes rather than padding |
| same | `.claude/skills/story-deslop/SKILL.md` | Preserve hooks, clues, character voice and causal anchors; bounded local polish |

Do not transplant host paths, adapters, production/video scaffolding, whole analysis pipelines, universal hook/turn frequencies, word blacklists, deletion percentages or fixed camera/dialogue ratios. Learn mechanisms, not copied story material. Long-form, short-form and screenplay keep distinct guidance; a quiet literary scene is not failed short-video drama.

## Verification and limits

Regression checks cover missing/inactive/other-type configurations, primary DB reload, actual MessageManager assembly, unchanged tool names/folder IDs/file streaming, reviewer read-only scope and runtime review threshold, skill-package parser fences and user resources, content-only CAS and existing activation/catalog/selection propagation. The SSE regression verifies that missing configuration never invokes the model workflow.

The three candidate DB configurations and thirteen actual official skill identities are also rendered/read back in an isolated database before rollout. Shared-role guidance removes fixed craft quotas and prioritizes intent/canon/causality. Policy assertions and mocked routing tests protect contracts; they do **not** prove generated prose quality or guarantee a literary score. Review cases include planning-only, one-chapter scope, quiet endings, fair reveals/local payoff, POV/state continuity, one-format screenplay, preserve-set polishing and original source transformation.

## Billing integration

Pricing and billing use the existing payment-order flow when checkout is enabled, with redemption codes and points as additional activation paths. Payment processing and benefit fulfillment are distinct states; the browser must not turn a pending return into a new payment. Current author-facing instructions live in [Plans & Benefits](../apps/web/docs/user-guide/billing-benefits.md). Preserve quota and refund accounting when changing writing configuration.

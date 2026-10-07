# M11 — vector search and context module

Independent whole-module verdict: **MODULE SOURCE_APPROVE / CLEAR**. Context assembly, vector/hybrid retrieval, ownership validation and index lifecycle leaves were reviewed; exact four-path repair integrated with zero conflicts.

## Minimal repair and measurements

Context and document consumers reuse `File.get_metadata()` for dictionary-or-empty compatibility, without changing stored JSON or DTOs. Entity extras are flattened before assigning authoritative entity ID/type/title; legacy metadata cannot misattribute a same-project result or suppress it via type mismatch. This was not a foreign-project leak claim.

Ownership validation selects only project ID, deleted state and file type, preserving every existing scope/type/normalization check. Cold 32/256-result measurements eliminated 32/256 File identities and measured 2/16 MiB ORM body materialization. Validation SELECT counts stay 32/256: no batching, transferred-byte, RSS or production-p95 claim. Real local Chroma/MockEmbedding rebuild, incremental indexing, context assembly and scalar SQL were exercised.

## Validation

Evidence: native `parallel-native-tmux/delete/m11-metadata-projection-proof` and root `m11-root-integration` under `/private/tmp/zenstory-module-audit-evidence-20261006`. Corrected baseline: 19 genuine metadata/identity failures, two prospective body-budget failures and 18 healthy controls; an invalid lore-title oracle was corrected before source edits. Native 39 PASS and separate 127 related PASS; final native and fresh actual-root **180 PASS, combined two-module coverage 83.82445% >= unchanged 80**. Vector alone is 77.64%, not independently >=80. Ruff/compile pass; equal-context mypy 55→55 legacy diagnostics, zero added, **not global green**. Late-import 63.22% instrumentation was not an enforced gate.

## Preserved architecture and limits

Existing bounded context capacity, auth, rebuild single-flight and shared file/history invariants remain. Queue/Chroma lifecycle races, eventual index consistency, retention, N+1 validation and strict wire-byte limits remain explicit nonblocking watches. No framework, queue, lock, schema or dependency addition. Final aggregate coverage/types/build/E2E, CI and deployment approval are separate.

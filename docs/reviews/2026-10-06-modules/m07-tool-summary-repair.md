# M07 tool summary projection repair — native lane, 2026-10-06

This bounded candidate follows independent DESIGN_CLEAR and awaits root SOURCE review. Root remained read-only. Only CRUD/serialization, the owned query projection test and this new attributed doc change. Phase evidence: /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/delete/m07-tool-summary-repair/REPORT.md.

query_files now reuses its exact-ID/page predicates and selects real File with content deferred plus a separate bounded SQL preview for eligible summary. Existing ordering/offset/limit/ownership and metadata filtering after paging remain. Preview0 is literal empty; PG substr is bounded; SQLite CASE detects embedded NUL and carries full separate value for Python slicing only on those rows. No persistent File.content assignment/with_expression. Full/invalid/unknown dialect retain full-row/per-row serializer behavior, including empty success and full ignoring bad preview.

Serializer accepts an optional preloaded preview, excludes body from summary model_dump and preserves Python slicing, all shallow fields/raw metadata/timestamps. Eligibility reuses normalizers without raising early. Source refs: crud.py:855/953/1085; serialization.py:21/65/96. All other CRUD functions and normalizers/exports preserve exact AST/function text; prior canonical/delete/writer repairs protected.

Actual registered MCP regression before source:10 prospective performance-budget RED,10 controls PASS,1unconfiguredPG skip; payloads matched before budget failures. Fresh20SQLitePASS; affected101PASS plus43related CRUDPASS, exact two-module coverage398/467=85.22%>=80. Final ownedPG14.22 Unicode/page/body-WHERE parity1PASS/18calls, no configured skip. Root owns mandatory CI path; no CI15/global gate claim.

Ordinary32/256 summaries reduce cold ORM full-body loads2MiB/16MiB to0 and carry11,808/93,996 separate preview UTF8 bytes; querycount stays2 (permission+oneFile). Default50 carries18,450 preview bytes instead of3.125MiB full bodies. NUL fallback is explicitly unbounded for that separate value; zero remains empty. No production p95/RSS/network-byte/TOAST promise. Warm summary/full/title-write keeps original content.

Invalid0-testPG raw-URL guard and initial coverage including6unowned modules retained/classified; coverage corrected to exact owned source without exclusions/threshold change. Ruff/compile pass; source mypy4legacy->4/0added NOTGREEN, scoped test-policy types pass. Current root conftest/dependencies exact and only two child candidate imports verified.

Owned Sessions/listeners/profiles/context/SQLite/cache resources cleaned; all3absent-owned PG leases normalDROP0sessions/catalog0/shared1/noForce. Prior immutable proof preserved. No source/config/schema/deps/provider/auth/retention/unrelated files changed. Root owns SOURCE approval/integration/module/all23/CI/publication; native lane stops after frozen uncommitted handoff.

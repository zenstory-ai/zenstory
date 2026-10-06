# M01 SSO access-token validation — bounded regression-first plan

User asks allmodule A/P/B review and fix; M01backend native read-only map already
identified validate-token querying User without is_active. Main active-user
constraint and OAuth existing-account callback reject disabled accounts. Prove
real HTTP mismatch after token issuance→admin-style deactivation using only own
SQLite fixture, local signed JWT and no providers/login/OAuth.

Ownership: root api/oauth.py validate_token only + new test_api module/review doc;
backend/frontend native maps read-only, source immutable to them until handoff.
Before editing, freeze root source and define2activepositive/2inactiveRED controls
with verified/unverified email to avoidinventing mandatory verification. Preserve
invalid/missing-user/refresh rejection, querytransport, DTOactive, no fresh token
or otherstate mutation. Minimum repair if genuineRED: after presentUser gate,
reject !is_active using existing AUTH_INACTIVE_USER/400 contract. No policy changes
to email_verified or token revocation; no broad worker/auth refactor in this slice.

Run targeted actualHTTP matrix first, then affected existingOAuth/authcompatibility
as necessary, scopedRuff and baseline-awaretypes/source review. Localfirst/no
Actions/push/install/DBshared/prod/provider mutation. Native M01worker/authqueue
candidates remain separate; allmodule/releaseclosure notclaimed.

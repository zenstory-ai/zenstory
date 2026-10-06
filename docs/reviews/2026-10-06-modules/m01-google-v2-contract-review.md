# M01 R5 — chosen Google v2 userinfo contract

Current source uses `https://www.googleapis.com/oauth2/v2/userinfo`, not the OIDC
userinfo endpoint. The chosen API's current official discovery schema defines
`Userinfo.verified_email` as a boolean, default true, and describes its returned
primary email as always verified. The registered method path is oauth2/v2/userinfo.
[Google OAuth2 v2 discovery](https://www.googleapis.com/discovery/v1/apis/oauth2/v2/rest).

Consequently, absent an explicit local policy requiring a runtime assertion, the
map's lack-of-check candidate does not establish that this real provider path can
supply an unverified account address. A mocked false/missing field alone would
not prove a production bug; missing data also has the documented default. No
blind `email_verified` OIDC-field guard or provider-version migration is introduced.
This is KEEP for the current documented provider contract, not a proof of every
email-reassignment or future OAuth account-linking scenario. A future API migration
must re-evaluate its own authoritative-email and field contracts.

Read-only primary docs checked locally during root review; no authenticated Google
calls, userinfo/token request, credentials, dependency, provider settings or remote
Actions. Specialist slots were already occupied/thread-limited; ordinary direct
primary-source lookup was used, not fabricated researcher/Conductor authority.

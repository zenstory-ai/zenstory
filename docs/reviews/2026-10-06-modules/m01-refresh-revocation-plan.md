# M01 refresh versus logout-all — actual PG proof first

MapR3: refreshlocks oldJTI row, logoutselects active rows thenupdates them. It may
miss refreshdescendant inserted after its SELECT while waiting on oldrecord.
Sequentialtests do not prove concurrency. Native now owns regression-only proof;
coreworker4ownedpaths immutable during parentintegration/freshgates.

New owned tests/test_services/test_auth_refresh_revocation_postgres.py; evidence
native delete/m01-refresh-revocation-proof. No productedit. Compare Root/child
relevantrefresh/logout/_revokehelper functiontext, not staleRegister/OAuthwholefile.
Use existingserializedPG env conventions and own leased UTF8 TEMPLATEtemplate0/C
DB on approvedexistinglocalhost55439 only. No sharedreset/newserver/termination.
ValidUser/access/refresh records first; actualJWT/handlers/ORM/locks unmocked.
Two cold productionexpiry requestSessions in owned eventloops/threads. A actual
refresh locks oldJTI then owned listener/event pauses. B actuallogout observes old
active rows and blockedUPDATE; inspect actualPGwait withinownDB. ReleaseA so its
newdescendant commits, Bfinishes; freshreader and actualchildrefresh validate
logout-all contract. No fake sleeps/lock-return substitutes/authoverrides; override
get_session only. CapturefirstgenuineRED +currentsequential/reverse/otherUser
controls asneeded. No perUsergate implementation before proof/advice.

Frame/eventSQL/rawfakeaccount trace, exactsourcehashes/commands/defaultexpiry and
rolledbackcleanup. Stoptests/listeners/writers beforeonlyownDBdrop0sessions/catalog0
/sharedhealth1. Proposed smallestexistingUserrow/familyserialization afterproof
mustbe separatelydesigned with User→Token lockorder/inactive/password/KEYSHARE
compatibility; no accessJWT blacklist/schema/gaplock/globalframework. Password
variant onlyif distinctneeded; no broadenedpolicy invented. LocalActionsconstraints,
no providers/email/credentials/install/prod/remote mainCI. All23 remainsmandatory.

## Root execution and bounded design (2026-10-06)

Native test written but not executed. Root five relevant function bodies matched
byte-for-byte before copying only the new test. Real owned UTF8/template0/C DB:
**one genuine concurrent RED /three sequential/other-user/access controls GREEN**.
B's UPDATE waited on A's real old-token FOR UPDATE; B selected old JTI only,
A committed a descendant and B finished logout200; new descendant subsequently
refreshed200 instead of401. Existing old-token replay was deliberately not used.
Evidence `m01-refresh-revocation-root/red.{log,xml}`, baseline/command/exit;
cleanup zero sessions/catalog, shared health1, no termination.

Proposed repair, pending independent design advice: acquire durable User gate
before token locks/active-token enumeration. Refresh SELECT User FOR UPDATE with
populate_existing, replacing cached get; revoke helper SELECT User.id FOR UPDATE
scalar gate only (do not reload pending password fields). Then existing helper
covers logout/password and existing refresh replay/expired semantics share
User→Token order. No new schema/dependency/helper framework/stateless access
blacklist or auth-new-intent login/verify/OAuth policy. SQLite concurrency remains
unpromised. Tests must now observe B waiting on the User SELECT, release A, then
prove B selects only the newly active child and rejects its refresh after commit;
old rotated row remains rotated. Preserve immutable original RED receipts and
existing sequential/other-user controls; add a bounded actual-password variant
only to prove this same helper, with bcrypt/mail boundary local fake as needed.

Independent architect DESIGN **CLEAR**: preserve scalar revoke projection, and
capture `utcnow()` only AFTER waiting User lock to avoid false revocation chronology.
Refresh must own User before JTI; password autoflush UserUPDATE is its gate. Native
source preserved; root now owns approved two-query repair and redesigned tests.
Required PG cases: actual blocked UserSELECT logout; blocked dirty UserUPDATE
password; original sequential/other-user/stateless access; empty-active logout
releases lock with no token mutation. Baseline RED receipt remains immutable.

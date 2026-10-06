# M01 auth establishment lifecycle repair

A pending password login or email verification could complete after logout, account replacement or provider unmount and persist its old credentials. Both methods incremented the shared auth generation only after API success. The old provider could also overwrite external storage while a replacement provider continued to display another account.

Both methods now reserve the existing generation before awaiting the API and reject superseded successful responses with `DOMException('Auth establishment superseded', 'AbortError')` before token/cache/user/analytics effects. The existing initialization effect invalidates its generation on cleanup without clearing shared storage. Login and VerifyEmail suppress only DOMException/AbortError in their existing catch blocks; the existing finally still releases local loading. Ordinary ApiError, TypeError, other DOMException and similarly named plain-object errors retain their prior behavior.

Latest-started intent wins: A remains superseded when newer B starts and fails before A succeeds. Completed server operations are not aborted. Promise<void> remains the outward type, with cancelled late success rejected so actual callers stop before success/navigation/error UI/code clearing. No new refs, stores, framework, dependencies or global config changes were introduced.

The bounded source changes affect AuthContext, Login and VerifyEmail only. Byte comparison against the phase baseline verifies the two method fences and initialization cleanup are the only AuthContext changes, and each caller has only one additional catch line. Loading-finally, other initialization/cache rules, refresh, OAuth callback, provider and SSO implementations are otherwise unchanged. Registration preflight, verification status/automatic-submit timers, SSO/OAuth continuation and other M01 leaves remain separate work.

Regression proof used actual AuthProvider/useAuth/native happy-dom Storage in default and actual root StrictMode. StrictMode seeded mounts assert two validation calls versus one by default. Tests retain the prior sixteen storage/user/analytics lifetime regressions and eighteen controls, add four newer-failed-request orderings, preserve exact ApiError/TypeError rejection identity, and exercise actual caller cancellation/error handling.

| Evidence | Result |
|---|---|
| Untouched-source pre-repair matrix | 74 executed: 24 valid RED, 50 controls GREEN, zero skips |
| Final affected eight suites | 228 GREEN, zero failures/skips |
| Final provider lifetime/order cases | 20 exact AbortError rejections; zero changed storage values, stale success events or A identifications; API A invoked once each |
| Scope: AuthContext/Login/VerifyEmail | statements 82.16%, branches 73.66%, functions 86.95%, lines 83.52% |
| Existing aggregate thresholds | statements 66%, branches 55%, functions 61%, lines 68%; unchanged, all pass |
| Forced source / owned-test types | Both exit 0; evidence-only build info |
| Affected six code/test files ESLint | Exit 0, max-warnings0 |
| Single Vite/Tailwind source build | Exit 0; explicit actual-source Tailwind base, envDir:false, evidence-only output/cache |

Evidence directory: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m01-auth-establishment-repair`. REPORT records exact commands, before/after measurements, seven-path baseline patch/hashes, fixture provenance and preservation proofs. Phase1 proof is unchanged in the sibling directory. The five root-supplied files matched before editing; the lifecycle test baseline was the phase1 child file, not yet in root. Prior six approved M05 paths and unowned auth/config files remain byte-identical.

No physical browser, backend, real credentials, OAuth/email/provider, production latency or full M01 approval is claimed. Local Node25/happy-dom differ from CI Node20 and physical browsers. The evidence fixture disables Node native webstorage for fork workers, retains native Storage rather than the shared setup's fake, denies default fetch before app imports and uses explicit fake API responses. There were zero default-fetch denials in the final run. Source/QA type configs and caches are confined to evidence; no installs or shared cache writes were requested.

The build validates App assets; unchanged static org/docs post-build steps were not rerun. Unrelated M05 test gates were not repeated. The existing aggregate coverage contract permits per-file variation (Login functions 58.33%); unexercised project-list branches/mobile/password-support/policy leaves are not declared closed by this slice. Actual cross-tab, cold-init/failed-establishment interleavings beyond current controls, and other mapped WATCHs remain separate evidence gaps.

Independent DESIGN CLEAR and source implementation authority preceded the repair. Root independent source review, narrow integration, all-module gates and release remain pending; M01/all23 are not declared complete. MainCI/remote Actions remain queued and unstarted. Source is left uncommitted for root handoff.

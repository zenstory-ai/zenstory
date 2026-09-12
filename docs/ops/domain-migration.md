# Organization / workbench domain migration

## Contract
- `https://zenstory.ai/`: crawler-readable ZenStory AI organization home.
- `https://app.zenstory.ai/`: original workbench. Existing localStorage sessions do not move across origins; users may need to sign in again. Do not copy stored tokens into URLs.
- Project/glossary/docs/legal pages stay on apex. App requests for them redirect to apex.
- Legacy apex auth/dashboard/project/materials/admin/onboarding/pricing URLs redirect to app, preserving path/query. The OAuth fragment must survive browser redirects.
- `/api/*` on both hosts proxies `https://api.zenstory.ai/api/*`; `/api/health` is an upstream JSON404, not the actual `/health` endpoint.

Source of routing truth: `apps/web/content/site-routing.json` and `scripts/build-site-layout.mjs`; regenerate tracked `vercel.json` with `node scripts/build-site-layout.mjs --write-config`. Build fails if it drifts. The controlled root shell must contain no canonical, og:url or JSON-LD: generators fail closed on inherited route metadata instead of trying to sanitize arbitrary HTML with regex. Vercel uses `npm run build:vercel`: Vite -> org -> docs -> domain finalizer. Default `npm run build`/Docker/local preview retain their root app index, robots.txt and legacy sitemap and skip Vercel-only relocation. No root index/robots/sitemap may survive finalization because files take precedence over Vercel rewrites. Private app routes stay out of sitemaps. Owned geo-preview/app-preview aliases are noindex validation surfaces, not canonical sites.

## Release order
1. Record current production deployment and nonsecret origin config.
2. Attach app host to ergou-ai; append app origin to Railway CORS, keeping all existing origins. Verify exact deployment SUCCESS, /health and valid/invalid CORS.
3. Validate source/tests, Vercel preview host matrix, no-JS/JS heads, internal aliases, client navigation and safe OAuth fragment. Never infer platform routing from local matcher tests alone.
4. Release verified web build. Confirm legacy apex `/auth/callback` permanently redirects to app and preserves harmless fragment.
5. Only now set Railway `FRONTEND_URL=https://app.zenstory.ai`. Leave `GOOGLE_REDIRECT_URI=https://api.zenstory.ai/api/auth/google/callback` unchanged. Poll exact deployment; retest callback error/anonymous flows and API proxy.
6. Record actual release IDs, test outputs and any authenticated-account/Search Console gaps. Do not claim successful real-account auth or search indexing without evidence.

## Controlled live smoke checks
The domain suite is excluded from normal Playwright discovery and never runs against a live host by default. Run from `apps/web`:

```sh
GEO_DOMAIN_MODE=preview npm run test:geo
# After the release and callback-first backend switch:
GEO_DOMAIN_MODE=production npm run test:geo
```

Both modes exercise read-only pages, anonymous guards and a harmless `access_denied` callback; neither signs in or creates user data. Preview roots must return `X-Robots-Tag: noindex`; production roots must not. Set `GEO_SITE_ORIGIN` and `GEO_APP_ORIGIN` together to override targets. Use `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` if needed for an installed browser. Remove temporary preview aliases only after verification; record that cleanup and retain noindex until removal.

## Baseline rollback (2026-09-12)
- Vercel existing project `ergou-ai`, project ID `prj_x4aJCaG3j8QHvwJKOpL3qeE6Lvl3`, prior production deployment `dpl_GjopJQmxLQpYZWKzn1KZC3uaCEVF` (main `6e67941`). Inspect before reuse: another legitimate release may supersede this baseline.
- Railway project `cbe4812b-c4fd-4d4d-ae13-b06395b2c7f0`, service `server`, environment `production`; previous `FRONTEND_URL=https://zenstory.ai`.
- Previous CORS: apex, www and manga origins. App origin was added (staging deployment `3b67233d-747e-4199-b7d7-ff8e5e1400b5`) without removing them.

On regression: restore FRONTEND_URL to recorded prior value first, then roll back Vercel to verified previous production deployment. Keep additive app CORS during rollback so staged app doesn't break; no DB/schema/provider-credential changes belong in this migration. Verify rollback with actual health, callback and host responses. Keep legacy permanent redirects for long-lived old links after successful cutover.


## Generator coverage
`npm run test:site:coverage` requires Node20.11+ (native LCOV reporter). It retains the spawned-generator integration tests and emits `coverage/site-lcov.info`; no third-party coverage package is added. CI runs it after Vitest coverage, asserts that all three generator source files have measured hits (so subprocess collection cannot silently disappear), and uploads both LCOV files together under the existing frontend flag. A green unit suite alone must not be reported as a green Codecov patch check; inspect the actual PR check before merge.

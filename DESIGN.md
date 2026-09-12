# Design
## Source of truth
Draft 2026-09-12; applies to the organization home and current static organization corpus only. Evidence: apps/web/scripts/org-pages.css, build-org-pages.mjs, content/org.json and projects.json, public brand SVG. Existing UI browser inspection unavailable through CUA; build/automated screenshots will be required. Does not redesign the workbench.
## Brand
An editorial, technically credible open-source story studio. Warm paper, serif headings, quiet orange accents; concrete capabilities/source evidence rather than AI hype. Reuse ZenStory AI mark. Avoid fabricated metrics, badges, testimonials, generic hero gradients.
## Product goals
Make the six projects and their distinct jobs understandable; guide people from a story format to a fitting tool; clearly separate organization identity from the web workbench. Success: first HTML tells who/what/source/next step; all cards and app CTA work.
## Personas and jobs
Chinese web-fiction authors, short-drama creators, agent-skill users, game adaptation developers, people who want a browser workbench. Visitors compare formats and installation surface before choosing.
## Information architecture
Home -> choose by output / six projects -> source/install and documentation. Main nav: Projects, Glossary, Docs, GitHub, Open app. Existing six detail routes and glossary/docs paths stable. Bilingual adjacent paragraphs, not fake language URLs.
## Design principles
Clear identity before details; concrete projects before broad claims; no-JS-first; reuse existing layout/styles; preserve workbench.
## Visual language
Existing org.css tokens for paper, ink, rule, accent; Source Serif/Plus Jakarta/Noto Serif with system fallbacks. Max content width 880px, readable prose 68ch, 14px grid gap, 6-8px controls/cards. No motion necessary, no invented imagery dependency.
## Components
Reuse top navigation, eyebrow, lede, actions, buttons, cards, columns, roster, bottom footer. Add one homepage composition, useful task chooser text, and explicit migration notice. Ownership in existing org stylesheet/generator; no parallel component library.
## Accessibility
Semantic header/nav/main/article/footer, one H1, ordered headings, language annotations, visible keyboard focus, meaningful link names, no color-only meaning. Target WCAG AA contrast; test light/dark accent buttons and mobile nav. No animated interactions.
## Responsive behavior
Desktop 1440px and mobile 390px. Existing 640px breakpoint; nav wraps to avoid overflow after app CTA. Cards single-column on narrow screens; tables locally scroll instead of entire page. Touch targets at least comfortably padded.
## Interaction states
Static page has no loading state or JS dependency; fonts gracefully fall back. Missing apex path gets genuine 404 plus home/app links. App CTA explains fresh sign-in may be needed; no token transfer.
## Content voice
Specific, modest, bilingual. Every capability from owned repository content. Dated counts only if displayed; no guarantee of ranking, AI citations, profit or automated production quality.
## Implementation constraints
Existing Node static generator, Vite app, no new dependencies. Initial HTML/metadata and human view must agree. Automated output assertions plus screenshots/a11y smoke; no user outreach.
## Open questions
- [ ] Authenticated browser session unavailable: verify successful login only if existing authorized session becomes available; don't claim it.
- [ ] Search Console access not established: index coverage remains unverified and separate from deployment.

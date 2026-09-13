# Design
## Source of truth
Revised 2026-09-13 for the organization site (zenstory.ai): home, /projects, /guides, six project pages, practical guides, /compare, glossary. Implementation: `apps/web/scripts/build-org-pages.mjs` (single-file generator, template strings) and `apps/web/scripts/org-pages.css` (plain CSS, copied to `/org/org.css`). Content is read from `apps/web/content/*.json` and is never changed for presentation. Does not cover the React workbench or the `/docs` prerender (`build-docs-pages.mjs`), which still use the app theme — see Open questions.
## Brand
An open-source story studio that reads as edited and credible. The palette comes from the brand mark: navy `#081431` is the ink for headings, primary buttons and the footer band; cream paper `#faf7f0` with `#f3ecdb` chapter bands; cyan `#22D3EE` only for tiny accents (star glyph, active-nav underline, dark-mode focus ring); warm orange `#b3542e` / text `#9a4523` for eyebrows, actions and link hover. Dark scheme maps the same roles. Reuse `public/brand/zenstory-ai-mark.svg`; social preview is `public/brand/og-zenstory-ai.png` (1200×630). No stock imagery, gradients, badges, testimonials or invented metrics.
## Product goals
First screen answers "what is this, is it free, how do I start": positioning line, one-sentence lede, at most two CTAs, the install command with a copy affordance, and a proof row (stars · MIT · projects · agent hosts, dated once). Make the six projects and their jobs legible, route people from a story format to a tool, keep the organization separate from the hosted workbench, and keep every GEO signal (bilingual text, JSON-LD, canonical, dated fixed-commit sources) in the initial HTML.
## Personas and jobs
Chinese web-fiction authors arriving from an AI answer on a phone; short-drama, game and video creators; English Claude Code users looking for non-coding skills; people who want the browser workbench. They compare formats and install surface before choosing.
## Information architecture
Home → choose by task / six projects → project page (what you need, start in three steps, its guides, Q&A, sources) → guide. Main nav: Projects · Guides · Glossary · Docs · GitHub · EN/中文 · Open app. `/guides` indexes every guide grouped by project. Existing routes are stable; bilingual content lives on one URL, never on language-prefixed URLs.
## Bilingual delivery
Both languages are always in the HTML, English first then 中文, wrapped by the generator helpers `pair()` (block), `both()` (inline) and `heading()`. An inline classic script sets `<html data-lang="en|zh">` from `localStorage['zs-lang']` or `navigator.language`; CSS shows one language; the header switch changes it. Without JavaScript nothing is hidden, so crawlers, the no-JS smoke tests and `geo-diff` see the complete corpus. Content that exists in one language only (Chinese glossary terms, code, links, tested `<h1>`s) is never wrapped.
## Design principles
Style and wrap, never edit asserted nodes: the generator tests pin exact tags, ids, class values, JSON-LD order and the sitemap count. One type system, one measure, disciplined color. Reduce link density: prose links are ink with a hairline underline; navigational lists and card titles are ink 600 without underline; orange text only for eyebrows and buttons. Show the same object the same way on every page (one card component, one guide list, one proof chip).
## Visual language
Body: Plus Jakarta Sans with a system CJK sans stack for both languages, 16px/1.65 (ZH 1.8, +.01em); headings: Source Serif 4 with system CJK serif fallback (h1 clamp 34–50px, h2 26px with a hairline rule and 64px top margin, h3 19px); mono: JetBrains Mono for commands. Google Fonts load non-render-blocking (preload + print-swap + noscript); Noto Serif SC is not loaded. Measure 720px shared by both languages; page wrap 1120px; 24px gutters; cards 1px borders, 8–10px radius, no heavy shadows. Alternating paper/cream bands mark home chapters.
## Components
Header (sticky, translucent with a plain fallback, `--top-h` drives `scroll-margin-top`), skip link, language switch, proof chips, copy-able install block, task cards, project cards, guide cards and guide list rows (flex, arrow never wraps), "What you need" table and "Start in 3 steps" rail on project pages, numbered Q&A list, compact two-column source notes, answer block on guides (`.pair.answer`), side-rail table of contents ≥960px, manuscript example panel, glossary two-column index, compact roster band, four-column navy footer. All generated from the JSON; every link the old pages had is still present.
## Accessibility
One `<h1>` per page, ordered headings, `nav[aria-label]`, `main#main`, `lang` on every Chinese node, visible `:focus-visible` on links and buttons (ink ring in light, cyan in dark), AA contrast for text including muted text (#626779) and dark-mode buttons, header controls ≥40px on phones, tables readable on mobile, no motion required. Verified with axe-core on home, project, guide, comparison and glossary pages in both schemes.
## Responsive behavior
Desktop 1440 and phone 390 are the reference widths; 640px and 960px breakpoints. Cards go 3 → 2 → 1 columns; the guide TOC becomes static in flow on phones; the header stacks to brand + switch + CTA over a link row within 96px; nothing scrolls horizontally except code and tables inside their own containers.
## Interaction states
No JavaScript is required to read anything. The language switch and the copy buttons are hidden until the classic script runs. Missing paths remain platform 404s. The workbench CTA explains that sign-in may be needed; no token transfer.
## Content voice
Specific, modest, bilingual. New UI copy is neutral labels or derived from JSON fields (install command, entry command, host list, dates); no ranking, citation, profit or quality guarantees.
## Decisions recorded 2026-09-13
- Install commands wrap onto a second line inside cards and tables (tokens never split; the Copy button has its own column) instead of the earlier nowrap-and-scroll rule, because a clipped command reads as broken.
- The Open Graph image is a 1200×630 PNG card (`/brand/og-zenstory-ai.png`) instead of the 32px SVG mark; this is the one intentional metadata change versus the previous pages, and the GEO diff allow-lists it.
- Bilingual inline pairs carry a real " · " separator text node (hidden in single-language modes) so text extractors never see glued tokens.
- The visible "as of <date>" appears once per hero; per-card copies are visually hidden but kept in the HTML, and the org-wide proof sentence keeps its own date because it is content.
## Implementation constraints
Single-file generator, plain CSS, no new dependencies or files the tests do not copy, no new routes without updating `content/site-routing.json`, `vercel.json` (`--write-config`), `llms.txt` and the sitemap count. Every change is verified with the generator tests, a GEO diff against a baseline build (metadata, JSON-LD, links and text tokens must survive), 390px overflow checks, and axe.
## Open questions
- [ ] `/docs` is the React app shell (`PublicHeader`, blue theme) and looks like a different product next to the organization pages; align its header, tokens and footer in `src/` and the prerender style block.
- [ ] Palette moved from terracotta-on-paper to the mark's navy/cream/cyan with orange accents; owner sign-off recorded when the branch is merged.
- [ ] Search Console access is still not established; indexing remains unverified.

# Quality checks

Run from the repository root with the existing dependencies. Frontend development requires Node.js 20.19+ or 22.12+ and pnpm 10; backend checks use the project Python 3.12/3.13 environment. `check-quality.sh` selects `.venv312`, `.venv` or `venv` under `apps/server`.

```bash
./scripts/quality/check-quality.sh [all|backend|frontend|security|i18n]
```

## Exact helper coverage

| Selection | Commands |
| --- | --- |
| `all` (default) | Backend followed by frontend |
| `backend` | Ruff on `agent/ api/ services/`; MyPy on `agent/ api/`; Bandit and Safety when installed |
| `frontend` | CSS-token check, ESLint and `tsc --noEmit` |
| `security` | Bandit and Safety when installed |
| `i18n` | Heuristic search for hardcoded Chinese text |

Missing optional backend tools are skipped with warnings. A zero exit does not establish that every listed tool ran. The frontend helper's `tsc --noEmit` also does not replace the repository's TypeScript project build or generated-site checks.

Use the actual CI entry points for their contracts:

```bash
pnpm --dir apps/web lint
pnpm --dir apps/web lint:tokens
pnpm --dir apps/web lint:i18n-keys
pnpm --dir apps/web build:typecheck
pnpm --dir apps/web test:site
```

## Translation checks

`check-i18n-defaultvalue-keys.mjs` verifies that `t("ns:key", { defaultValue: ... })` calls in non-test frontend source have the key in both language namespaces. Translations live in `apps/web/public/locales/{zh,en}/{namespace}.json`.

The hardcoded-text shell checks identify candidates for manual review. They depend on `grep -P` support and skip several line patterns; their output is not a complete translation-coverage result. Keep API fields, examples and author text separate from UI labels when reviewing matches.

```tsx
// Bad - Hardcoded
<button>提交</button>

// Good - Using i18n
const { t } = useTranslation();
<button>{t('common.submit')}</button>
```

## CSS-token checks

`check-web-css-token-vars.mjs` scans frontend TS/TSX/JS/JSX/CSS for `var(--token)` references and CSS definitions. It fails when a referenced token has no definition; visual contrast, focus and layout still require the checks appropriate to the UI change.

The [development guide](../../docs/development/README.md) lists targeted test commands. CI workflow YAML and release controls have their own checks under `scripts/ci/`.

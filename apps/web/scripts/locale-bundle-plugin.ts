import fs from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'
import {
  LAZY_LOCALE_NAMESPACE_SET,
  LOCALE_LANGUAGES,
  type LocaleLanguage,
} from '../src/lib/localeNamespaces'

const virtualPrefix = 'virtual:zenstory-locale-'
const resolvedPrefix = `\0${virtualPrefix}`

type ParsedLocaleTarget =
  | { language: LocaleLanguage; kind: 'core' }
  | { language: LocaleLanguage; kind: 'lazy'; namespace: string }

function parseLocaleVirtualId(suffix: string): ParsedLocaleTarget | null {
  const dashIndex = suffix.indexOf('-')
  if (dashIndex <= 0) return null

  const language = suffix.slice(0, dashIndex)
  if (!LOCALE_LANGUAGES.includes(language as LocaleLanguage)) return null

  const target = suffix.slice(dashIndex + 1)
  if (target === 'core') {
    return { language: language as LocaleLanguage, kind: 'core' }
  }
  if (LAZY_LOCALE_NAMESPACE_SET.has(target)) {
    return { language: language as LocaleLanguage, kind: 'lazy', namespace: target }
  }
  return null
}

/**
 * Builds code-split JavaScript modules from the existing public JSON locale
 * files:
 * - `virtual:zenstory-locale-{lang}-core`: aggregates all non-lazy namespaces
 *   needed for initial workbench/dashboard boot.
 * - `virtual:zenstory-locale-{lang}-{ns}`: single-namespace lazy chunk loaded
 *   on demand for routes/panes such as admin, privacy, materials, skills, etc.
 *
 * The public JSON files remain untouched for older clients and static tools.
 */
export function localeBundlePlugin(
  localesDir = path.resolve(process.cwd(), 'public/locales'),
): Plugin {
  return {
    name: 'zenstory-locale-bundles',
    resolveId(id) {
      if (!id.startsWith(virtualPrefix)) return null
      return parseLocaleVirtualId(id.slice(virtualPrefix.length))
        ? `\0${id}`
        : null
    },
    load(id) {
      if (!id.startsWith(resolvedPrefix)) return null

      const parsed = parseLocaleVirtualId(id.slice(resolvedPrefix.length))
      if (!parsed) return null

      const languageDir = path.join(localesDir, parsed.language)

      if (parsed.kind === 'lazy') {
        const file = path.join(languageDir, `${parsed.namespace}.json`)
        this.addWatchFile(file)
        const resource = JSON.parse(fs.readFileSync(file, 'utf8'))
        return `export default ${JSON.stringify(resource)};`
      }

      const bundle: Record<string, unknown> = {}
      for (const filename of fs.readdirSync(languageDir).filter((name) => name.endsWith('.json')).sort()) {
        const namespace = path.basename(filename, '.json')
        if (LAZY_LOCALE_NAMESPACE_SET.has(namespace)) continue
        const file = path.join(languageDir, filename)
        this.addWatchFile(file)
        bundle[namespace] = JSON.parse(fs.readFileSync(file, 'utf8'))
      }

      return `export default ${JSON.stringify(bundle)};`
    },
  }
}

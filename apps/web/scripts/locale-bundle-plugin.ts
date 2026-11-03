import fs from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

const languages = ['zh', 'en'] as const
const virtualPrefix = 'virtual:zenstory-locale-'
const resolvedPrefix = `\0${virtualPrefix}`

/**
 * Builds one lazy JavaScript module per language from the existing public JSON
 * files. The public files remain untouched for older clients and static tools.
 */
export function localeBundlePlugin(
  localesDir = path.resolve(process.cwd(), 'public/locales'),
): Plugin {
  return {
    name: 'zenstory-locale-bundles',
    resolveId(id) {
      return languages.some((language) => id === `${virtualPrefix}${language}`)
        ? `\0${id}`
        : null
    },
    load(id) {
      if (!id.startsWith(resolvedPrefix)) return null

      const language = id.slice(resolvedPrefix.length)
      if (!languages.includes(language as (typeof languages)[number])) return null

      const languageDir = path.join(localesDir, language)
      const bundle: Record<string, unknown> = {}
      for (const filename of fs.readdirSync(languageDir).filter((name) => name.endsWith('.json')).sort()) {
        const file = path.join(languageDir, filename)
        this.addWatchFile(file)
        bundle[path.basename(filename, '.json')] = JSON.parse(fs.readFileSync(file, 'utf8'))
      }

      return `export default ${JSON.stringify(bundle)};`
    },
  }
}

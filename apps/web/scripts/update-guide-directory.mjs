import { readFileSync, writeFileSync } from 'node:fs'
const web = new URL('../', import.meta.url)
const read = (name) => JSON.parse(readFileSync(new URL(`content/${name}.json`, web), 'utf8'))
const topics = read('guide-topics')
const items = [...read('guides'), ...read('articles')]
const path = new URL('public/llms.txt', web)
const text = readFileSync(path, 'utf8')
const start = text.indexOf('## Guides')
const next = text.indexOf('\n## ', start + 4)
if (start < 0) throw new Error('Missing Guides section in llms.txt')
const lines = ['## Guides', '', 'Guides index: https://zenstory.ai/guides (中文: https://zenstory.ai/zh/guides) — browse by creative task or search for a working question. Each published article includes a concrete method, an original example, and its skill source.', '']
for (const topic of topics) {
  lines.push(`### ${topic.title.en} / ${topic.title.zh}`)
  lines.push(`- [${topic.title.en}](https://zenstory.ai/guides/${topic.slug}) — ${topic.description.en}`)
  for (const item of items.filter((item) => item.topic === topic.slug)) {
    const lang = !item.langs || item.langs.includes('en') ? 'en' : 'zh'
    const route = `${lang === 'zh' ? '/zh' : ''}/${item.owner}/${item.slug}`
    // llms.txt lines are plain descriptions, not nested Markdown destinations.
    const description = (item.description ?? item.answer)[lang].replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/`/g, '').replace(/\s+/g, ' ')
    lines.push(`- [${item.title[lang]}](https://zenstory.ai${route}) — ${description}`)
  }
  lines.push('')
}
writeFileSync(path, text.slice(0, start) + lines.join('\n') + '\n' + (next < 0 ? '' : text.slice(next)))
console.log(`Updated directory: ${items.length} guides in ${topics.length} tasks`)

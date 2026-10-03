import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { vercelConfig } from '../build-site-layout.mjs'
const web = new URL('../../', import.meta.url)
const content = (name) => JSON.parse(readFileSync(new URL(`content/${name}.json`, web), 'utf8'))
const guides = content('guides'), articles = content('articles'), topics = content('guide-topics'), redirects = content('guide-redirects')
const reading = [...guides, ...articles]
const routeOf = (item) => `/${item.owner}/${item.slug}`
const pathOf = (lang, route) => lang === 'zh' ? `/zh${route}` : route
const OWNED_METHOD_SOURCE = /^https:\/\/github\.com\/zenstory-ai\/[^/]+\/blob\/[a-f0-9]{40}\/(?:packages\/knowledge\/[^/]+\/)?skills\/[^/]+\/(?:SKILL\.md|references\/.+\.(?:md|py|sh)|scripts\/.+\.py|assets\/.+\.md)$/
const OFFICIAL_METHOD_SOURCES = new Set([
  'https://kling.ai/quickstart/image-to-video-guide',
  'https://kling.ai/quickstart/klingai-video-3-model-user-guide',
  'https://ffmpeg.org/ffprobe.html',
  'https://ffmpeg.org/ffmpeg-filters.html#tonemap',
  'https://ffmpeg.org/ffmpeg-filters.html#zscale',
  'https://ffmpeg.org/ffmpeg-filters.html#setparams',
  'https://ffmpeg.org/ffmpeg-filters.html#fps',
  'https://github.com/FFmpeg/FFmpeg/blob/98e92563a3b60dbf6d370fd3491d7f896398e4c1/libavfilter/colorspace.h',
  'https://github.com/FFmpeg/FFmpeg/blob/98e92563a3b60dbf6d370fd3491d7f896398e4c1/libavfilter/colorspace.c',
])
const sourceUrlAllowed = (url) => OWNED_METHOD_SOURCE.test(url) || OFFICIAL_METHOD_SOURCES.has(url)
const articleSourcesAreValid = (sources) => sources?.some((source) => OWNED_METHOD_SOURCE.test(source.url)) && sources.every((source) => sourceUrlAllowed(source.url))

test('published articles link immutable specialist skill files and have explicit task categories', () => {
  const ids = new Set(topics.map((topic) => topic.slug))
  const repos = {'oh-story':'oh-story-claudecode', 'drama-skills':'drama-skills', 'novel-to-game':'novel-to-game', 'video-recap':'video-recap-skills', dsh:'oh-story-dsh'}
  const skills = {
    'oh-story':['story-setup','story-import','story-long-write','story-short-write','story-long-analyze','story-review','story-deslop','story-cover'],
    'drama-skills':['short-drama','short-drama-develop','short-drama-write','short-drama-novel-analyze','short-drama-image-prompts','short-drama-video-prompts','short-drama-storyboard','short-drama-review','short-drama-edit','short-drama-assets','short-drama-produce'],
    'novel-to-game':['novel-to-game','game-concept','game-world-design','game-build','game-qa','game-art-direction'],
    'video-recap':['video-recap','video-script','video-cut','video-assemble','video-voiceover'], dsh:['novel-to-game']
  }
  for (const item of reading) assert.ok(ids.has(item.topic), `${item.slug}: missing task assignment`)
  for (const lang of ['en','zh']) {
    const titles = reading.filter((item)=>!item.langs || item.langs.includes(lang)).map((item)=>item.title[lang].toLocaleLowerCase().trim())
    assert.equal(titles.length,new Set(titles).size,`${lang}: duplicated reader question`)
  }
  for (const article of articles) {
    assert.ok(skills[article.owner].includes(article.skill.name),`${article.slug}: invalid skill`)
    const prefix = article.owner==='dsh' ? 'packages/knowledge/novel-to-game/' : ''
    assert.match(article.skill.url,new RegExp(`^https://github.com/zenstory-ai/${repos[article.owner]}/blob/[a-f0-9]{40}/${prefix}skills/${article.skill.name}/SKILL\\.md$`))
    assert.ok(article.sources?.length,`${article.slug}: missing method source`)
    assert.ok(article.sources.some((source) => OWNED_METHOD_SOURCE.test(source.url)),`${article.slug}: missing owned method source`)
    for (const source of article.sources) assert.ok(sourceUrlAllowed(source.url),`${article.slug}: unapproved method source ${source.url}`)
    assert.ok(articleSourcesAreValid(article.sources),`${article.slug}: invalid method sources`)
  }
})

test('article source policy permits only pinned owned methods and exact approved provider and FFmpeg references', () => {
  const owned = 'https://github.com/zenstory-ai/video-recap-skills/blob/0123456789abcdef0123456789abcdef01234567/skills/video-assemble/SKILL.md'
  const accepted = [owned, ...OFFICIAL_METHOD_SOURCES]
  const rejected = [
    'http://ffmpeg.org/ffprobe.html',
    'https://www.ffmpeg.org/ffprobe.html',
    'https://ffmpeg.org.evil.example/ffprobe.html',
    'https://ffmpeg.org@evil.example/ffprobe.html',
    'https://ffmpeg.org/ffprobe.html?format=json',
    'https://ffmpeg.org/ffmpeg-filters.html#scale',
    'https://ffmpeg.org/ffmpeg-filters.html#fps?rate=30',
    'https://ffmpeg.org/ffmpeg-filters.html#fps_002c-minterpolate',
    'https://ffmpeg.org/ffmpeg-filters.html',
    'https://github.com/zenstory-ai/video-recap-skills/blob/main/skills/video-assemble/SKILL.md',
    'https://github.com/FFmpeg/FFmpeg/blob/master/libavfilter/colorspace.h',
    'https://github.com/FFmpeg/FFmpeg/blob/main/libavfilter/colorspace.h',
    'https://github.com/FFmpeg/FFmpeg/blob/0000000000000000000000000000000000000000/libavfilter/colorspace.h',
    'https://github.com/FFmpeg/FFmpeg/blob/98e92563a3b60dbf6d370fd3491d7f896398e4c1/libavfilter/vf_zscale.c',
    'https://github.com/FFmpeg/FFmpeg/blob/98e92563a3b60dbf6d370fd3491d7f896398e4c1/libavfilter/colorspace.h?raw=1',
    'https://github.com/FFmpeg/FFmpeg/blob/98e92563a3b60dbf6d370fd3491d7f896398e4c1/libavfilter/colorspace.h#L27',
    'https://github.com/FFmpeg-lookalike/FFmpeg/blob/98e92563a3b60dbf6d370fd3491d7f896398e4c1/libavfilter/colorspace.h',
    'https://github.com.evil.example/FFmpeg/FFmpeg/blob/98e92563a3b60dbf6d370fd3491d7f896398e4c1/libavfilter/colorspace.h',
    '',
    'https://example.com/ffprobe.html',
    'https://kling.ai/quickstart/text-to-video-prompt-guide',
    'http://kling.ai/quickstart/image-to-video-guide',
    'https://kling.ai.evil.example/quickstart/image-to-video-guide',
    'https://kling.ai@evil.example/quickstart/image-to-video-guide',
    'https://www.kling.ai/quickstart/image-to-video-guide',
    'https://kling.ai/quickstart/image-to-video-guide?ref=article',
    'https://kling.ai/quickstart/klingai-video-3-model-user-guide#pricing',
    'javascript:alert(1)',
  ]
  for (const url of accepted) assert.equal(sourceUrlAllowed(url),true,`should accept ${url}`)
  for (const url of rejected) assert.equal(sourceUrlAllowed(url),false,`should reject ${url}`)
  assert.equal(articleSourcesAreValid([...OFFICIAL_METHOD_SOURCES].map((url) => ({url}))),false,'official sources cannot replace the owned method source')
  assert.equal(articleSourcesAreValid(accepted.map((url) => ({url}))),true,'owned and exact official sources should be accepted together')
})

test('every article belongs to one paginated category; home stays bounded; merged links leave the catalog', (t) => {
  const out=mkdtempSync(join(tmpdir(),'zenstory-library-'))
  t.after(()=>rmSync(out,{recursive:true,force:true}))
  const result=spawnSync(process.execPath,[new URL('../build-org-pages.mjs',import.meta.url).pathname,out],{encoding:'utf8'})
  assert.equal(result.status,0,result.stderr)
  const directory=readFileSync(new URL('public/llms.txt',web),'utf8')
  const routes=new Set(reading.map(routeOf))
  for(const lang of ['en','zh']) {
    const home=readFileSync(join(out,lang==='en'?'org-home/index.html':'zh/index.html'),'utf8')
    const index=readFileSync(join(out,pathOf(lang,'/guides'),'index.html'),'utf8')
    assert.ok(home.length<80000,'homepage must not print the full content library')
    assert.ok(index.includes('data-guide-search hidden'))
    assert.ok(index.includes('data-search-results hidden'))
    for(const topic of topics) {
      const expected=reading.filter((item)=>item.topic===topic.slug && (!item.langs || item.langs.includes(lang)))
      const count=Math.max(1,Math.ceil(expected.length/24)),actual=[]
      assert.ok(index.includes(`href="${pathOf(lang,`/guides/${topic.slug}`)}"`))
      for(let page=1;page<=count;page++) {
        const route=`/guides/${topic.slug}${page>1?`/page/${page}`:''}`
        const html=readFileSync(join(out,pathOf(lang,route),'index.html'),'utf8')
        assert.ok(html.includes(`<link rel="canonical" href="https://zenstory.ai${pathOf(lang,route)}">`))
        const graph=JSON.parse(html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/)[1])['@graph']
        const links=graph[1].itemListElement
        assert.ok(links.length<=24)
        actual.push(...links.map((entry)=>entry.url.replace(`https://zenstory.ai${lang==='zh'?'/zh':''}`,'')))
        if(page<count) assert.ok(html.includes(`href="${pathOf(lang,`/guides/${topic.slug}/page/${page+1}`)}" rel="next"`))
        if(page>1) assert.ok(html.includes('rel="prev"'))
      }
      assert.deepEqual(new Set(actual),new Set(expected.map(routeOf)),`${lang} ${topic.slug}: missing article`)
      assert.equal(actual.length,expected.length,`${lang} ${topic.slug}: duplicate article`)
    }
    for(const article of articles.filter((item)=>item.langs.includes(lang))) {
      const html=readFileSync(join(out,pathOf(lang,routeOf(article)),'index.html'),'utf8')
      assert.ok(html.includes(`href="${article.skill.url}"`))
      for (const source of article.sources) {
        assert.ok(sourceUrlAllowed(source.url),`${article.slug}: generated an unapproved source`)
        assert.ok(html.includes(`href="${source.url}"`),`${article.slug}: missing rendered source ${source.url}`)
      }
    }
  }
  for(const [source,target] of Object.entries(redirects)) {
    assert.ok(!routes.has(source));assert.ok(routes.has(target))
    for(const lang of ['en','zh']) {
      assert.ok(!existsSync(join(out,pathOf(lang,source),'index.html')))
      const rule=vercelConfig.redirects.find((entry)=>entry.source===pathOf(lang,source))
      assert.equal(rule.destination,`https://zenstory.ai${pathOf(lang,target)}`);assert.equal(rule.permanent,true)
    }
    assert.ok(!directory.includes(`](https://zenstory.ai${source})`))
  }
  for(const item of reading) assert.ok(directory.includes(`](https://zenstory.ai${routeOf(item)})`))
})

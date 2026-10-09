import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { render, screen } from '@testing-library/react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { prepareCjkEmphasis, remarkStripCjkEmphasisMarkers } from '../cjkEmphasis'
import { LazyMarkdown } from '../../components/LazyMarkdown'

const renderPlain = (markdown: string) =>
  renderToStaticMarkup(<ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>)

const renderFixed = (markdown: string) =>
  renderToStaticMarkup(
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkStripCjkEmphasisMarkers]}>
      {prepareCjkEmphasis(markdown)}
    </ReactMarkdown>,
  )

describe('bold next to Chinese punctuation', () => {
  it('reproduces the CommonMark gap this works around', () => {
    expect(renderPlain('这是**《余额》**的开头')).toContain('**')
  })

  it.each([
    ['这是**《余额》**的开头', '<strong>《余额》</strong>'],
    ['他喊了一声**“站住”**就追了出去', '<strong>“站住”</strong>'],
    ['**《余额》**和**《头顶的钟》**都可以', '<strong>《余额》</strong>和<strong>《头顶的钟》</strong>'],
    ['主角叫**陈越（外卖骑手）**，二十岁', '<strong>陈越（外卖骑手）</strong>'],
    ['- 人物：**陈越**（主角）', '<strong>陈越</strong>'],
    ['倒计时**不是死亡时间**，而是“账”', '<strong>不是死亡时间</strong>'],
  ])('renders %j as bold', (markdown, expected) => {
    const html = renderFixed(markdown)
    expect(html).toContain(expected)
    expect(html).not.toContain('*')
    expect(html).not.toContain('\u200B')
  })

  it('leaves code, escaped asterisks and Latin text alone', () => {
    expect(renderFixed('`a**《b》**c`')).toContain('<code>a**《b》**c</code>')
    expect(renderFixed('价格\\*\\*《注》\\*\\*另计')).toContain('价格**《注》**另计')
    expect(prepareCjkEmphasis('foo**"bar"**baz')).toBe('foo**"bar"**baz')
    expect(prepareCjkEmphasis('没有加粗的一句话。')).toBe('没有加粗的一句话。')
  })
})

describe('literal tildes and underscores beside bold', () => {
  it.each([
    ['收到~**第一章**写好了，你看看~', '收到~<strong>第一章</strong>写好了，你看看~'],
    ['好~**真的**~', '好~<strong>真的</strong>~'],
    ['名字叫_**林默**_吧', '名字叫_<strong>林默</strong>_吧'],
  ])('keeps %j as written, without strikethrough', (markdown, expected) => {
    const html = renderFixed(markdown)
    expect(html).toContain(expected)
    expect(html).not.toContain('<del>')
    expect(html).not.toContain('<em>')
    expect(html).toBe(renderPlain(markdown))
  })
})

describe('LazyMarkdown', () => {
  it('shows 《书名》 in bold in chat replies instead of literal asterisks', async () => {
    const { container } = render(<LazyMarkdown>{'先定好**《余额》**的总纲，再写第一章。'}</LazyMarkdown>)
    const strong = await screen.findByText('《余额》')
    expect(strong.tagName).toBe('STRONG')
    expect(container.textContent).toBe('先定好《余额》的总纲，再写第一章。')
  })
})

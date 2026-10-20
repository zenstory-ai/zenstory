import { describe, it, expect } from 'vitest'
import { optionsOfferedInReply } from '../replyOptions'

const FIRST_REPLY_TAIL = [
  '- **偏厅衔接**：在段首补了半句「他到偏厅时，」，让场景从案前接到偏厅。',
  '',
  '第三人称限知、全角引号、段首缩进都照原样保留。',
  '',
  '想换个方向的话，可以回我一句：',
  '- 「三皇子手里的京营改成四万，太子那边也加上兵权」',
  '- 「开头再加一段，写他翻账册时发现有人动过手脚」',
].join('\n')

describe('optionsOfferedInReply', () => {
  it.each([
    ['the closing options of a first reply', FIRST_REPLY_TAIL, [
      '三皇子手里的京营改成四万，太子那边也加上兵权',
      '开头再加一段，写他翻账册时发现有人动过手脚',
    ]],
    ['options followed by the completion marker', '接下来可以：\n\n1. 「按设定写第二章」\n2. 「先列前三章大纲」。\n\n[TASK_COMPLETE]', [
      '按设定写第二章',
      '先列前三章大纲',
    ]],
    ['English options in curly quotes', 'You could reply with:\n- “Write chapter two”\n- “Make the prince older”', [
      'Write chapter two',
      'Make the prince older',
    ]],
  ])('reads %s', (_name, reply, expected) => {
    expect(optionsOfferedInReply(reply)).toEqual(expected)
  })

  it.each([
    ['a closing list of what was changed', '改好了：\n- **京营数字**：改成三万余\n- **太子措辞**：改成兵权最虚'],
    ['a single option', '想换个方向可以说：\n- 「把主角改成女性」'],
    ['more than three options', '可以回我：\n- 「一」\n- 「二」\n- 「三」\n- 「四」'],
    ['options not introduced by a colon', '他说了两句话。\n- 「走吧」\n- 「别回头」'],
    ['a sentence after the options', '可以回我一句：\n- 「写第二章」\n- 「改开头」\n或者直接告诉我想怎么改。'],
    ['an empty reply', ''],
  ])('ignores %s', (_name, reply) => {
    expect(optionsOfferedInReply(reply)).toBeNull()
  })
})

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  duplicatesNextStep,
  isNextStepDismissed,
  nextStepKind,
  rememberNextStepDismissed,
} from '../nextStep'
import zhChat from '../../../public/locales/zh/chat.json'

describe('duplicatesNextStep', () => {
  it.each([
    '写第一章，老周还剩七天',
    '先写第1集剧本，结尾留强钩子',
    '开始写正文',
    '按大纲动笔写开篇',
    '按大纲写第一章',
    'Write chapter 1 with the countdown',
  ])('treats %j as the same action as the card', (chip) => {
    expect(duplicatesNextStep(chip)).toBe(true)
  })

  it.each([
    '把陈越的算账性格写进开场',
    '先补第一章细纲',
    '写第一章大纲',
    '先补陈越的角色卡',
    '接着写第二章',
    '写第 10 章',
    'Add a rival to the outline',
  ])('keeps %j, which offers a different direction', (chip) => {
    expect(duplicatesNextStep(chip)).toBe(false)
  })
})

describe('next step card copy', () => {
  it('names the same unit in the line and the button for each project type', () => {
    const nextStep = zhChat.nextStep as Record<string, { frameworkReady: string; label: string }>
    expect(nextStep.screenplay.frameworkReady).toContain('剧本')
    expect(nextStep.screenplay.label).toContain('剧本')
    expect(nextStep.novel.frameworkReady).toContain('第一章')
    expect(nextStep.novel.label).toContain('第一章')
    expect(nextStep.short.frameworkReady).toContain('正文')
    expect(nextStep.short.label).toContain('正文')
  })

  it('falls back to the novel wording for unknown types', () => {
    expect(nextStepKind('screenplay')).toBe('screenplay')
    expect(nextStepKind('short')).toBe('short')
    expect(nextStepKind(undefined)).toBe('novel')
    expect(nextStepKind('novel')).toBe('novel')
  })
})

describe('next step dismissal', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('is remembered per project', () => {
    rememberNextStepDismissed('p1')
    expect(isNextStepDismissed('p1')).toBe(true)
    expect(isNextStepDismissed('p2')).toBe(false)
  })

  it('treats blocked storage as not dismissed instead of throwing', () => {
    vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => rememberNextStepDismissed('p1')).not.toThrow()
    expect(isNextStepDismissed('p1')).toBe(false)
  })
})

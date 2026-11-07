import { describe, expect, it } from 'vitest'
import zh from '../../../public/locales/zh/errors.json'
import en from '../../../public/locales/en/errors.json'
import zhChat from '../../../public/locales/zh/chat.json'
import enChat from '../../../public/locales/en/chat.json'

describe('account task limit copy', () => {
  it('uses the confirmed wording in Chinese', () => {
    expect(zh.ERR_ACCOUNT_RUNS_BUSY).toBe('已有任务正在进行，请等待完成或停止当前任务后再试。')
  })

  it('is complete in both languages', () => {
    expect(en.ERR_ACCOUNT_RUNS_BUSY).toBe('A task is already running. Wait for it to finish or stop it, then try again.')
    expect(zhChat.panel.accountRunsBusyTitle).toBeTruthy()
    expect(enChat.panel.accountRunsBusyTitle).toBeTruthy()
  })
})

import i18next from 'i18next'
import { beforeAll, describe, expect, it } from 'vitest'

import zhMaterials from '../../../public/locales/zh/materials.json'
import enMaterials from '../../../public/locales/en/materials.json'
import { materialEnumLabel } from '../materialEnumLabels'

const zh = i18next.createInstance()
const en = i18next.createInstance()

beforeAll(async () => {
  await zh.init({ lng: 'zh', resources: { zh: { materials: zhMaterials } }, ns: ['materials'] })
  await en.init({ lng: 'en', resources: { en: { materials: enMaterials } }, ns: ['materials'] })
})

describe('materialEnumLabel', () => {
  it('translates production enum values instead of showing raw identifiers', () => {
    expect(materialEnumLabel(zh.t, 'plotType', 'SETUP')).toBe('铺垫')
    expect(materialEnumLabel(zh.t, 'plotType', 'TURNING_POINT')).toBe('转折')
    expect(materialEnumLabel(zh.t, 'goldenFingerType', 'special_physique')).toBe('特殊体质')
    expect(materialEnumLabel(zh.t, 'storyType', 'cultivation')).toBe('修炼线')
    expect(materialEnumLabel(zh.t, 'relationshipType', 'master_disciple')).toBe('师徒')
    expect(materialEnumLabel(en.t, 'plotType', 'SETUP')).toBe('Setup')
  })

  it('keeps model-written Chinese values and hides unknown English identifiers', () => {
    expect(materialEnumLabel(zh.t, 'storyType', '宫斗线')).toBe('宫斗线')
    expect(materialEnumLabel(zh.t, 'storyType', 'space_opera')).toBe('其他')
    expect(materialEnumLabel(en.t, 'storyType', 'space_opera')).toBe('Other')
    expect(materialEnumLabel(zh.t, 'plotType', '')).toBe('')
  })

  it('covers every value the extraction validators accept', () => {
    const accepted = {
      plotType: ['CONFLICT', 'TURNING_POINT', 'REVEAL', 'ACTION', 'DIALOGUE', 'SETUP', 'RESOLUTION', 'OTHER'],
      storyType: ['main', 'romance', 'growth', 'revenge', 'treasure', 'conflict', 'mystery', 'other'],
      relationshipType: ['family', 'master_disciple', 'friend', 'enemy', 'lover', 'colleague', 'superior_subordinate', 'business', 'other'],
      goldenFingerType: ['system', 'space', 'rebirth', 'transmigration', 'special_physique', 'artifact', 'bloodline', 'other'],
    } as const
    for (const [group, values] of Object.entries(accepted)) {
      for (const value of values) {
        for (const t of [zh.t, en.t]) {
          expect(t(`materials:enums.${group}.${value.toLowerCase()}`, { defaultValue: '' }), `${group}.${value}`).not.toBe('')
        }
      }
    }
  })
})

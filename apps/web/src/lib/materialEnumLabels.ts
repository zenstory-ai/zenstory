import type { TFunction } from 'i18next';

/** 拆解结果里由模型产出的英文枚举（情节类型、剧情类型、关系类型、金手指类型）。 */
export type MaterialEnumGroup = 'plotType' | 'storyType' | 'relationshipType' | 'goldenFingerType';

const CJK = /[㐀-鿿]/;

/**
 * 把拆解结果里的枚举值翻成界面文案。已知值走 materials:enums.<group>.<value>；
 * 未知值若本身就是中文（模型直接给了中文）原样展示，否则显示「其他」——绝不把
 * SETUP、special_physique 这类原始英文标识直接给用户看。
 */
export function materialEnumLabel(t: TFunction, group: MaterialEnumGroup, value: string | null | undefined): string {
  const raw = (value ?? '').trim();
  if (!raw) return '';
  const key = raw.toLowerCase();
  const label = t(`materials:enums.${group}.${key}`, { defaultValue: '' });
  if (label) return label;
  if (CJK.test(raw)) return raw;
  return t('materials:enums.other', { defaultValue: '其他' });
}

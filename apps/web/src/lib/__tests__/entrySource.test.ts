import { beforeEach, describe, expect, it } from 'vitest';
import { getEntrySource, rememberEntrySource } from '../entrySource';

describe('entry source', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('keeps the first landing source in the tab', () => {
    rememberEntrySource('?lang=en&source=org_header');
    rememberEntrySource('?source=home_hero');

    expect(getEntrySource()).toBe('org_header');
  });

  it('ignores a missing or malformed source', () => {
    rememberEntrySource('?lang=en');
    rememberEntrySource('?source=https://evil.example/?x=1');

    expect(getEntrySource()).toBeUndefined();
  });
});

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Plugin } from 'vite';
import { afterEach, describe, expect, it } from 'vitest';
import { localeBundlePlugin } from '../../../scripts/locale-bundle-plugin';
import {
  CORE_LOCALE_NAMESPACES,
  LAZY_LOCALE_NAMESPACES,
  LOCALE_LANGUAGES,
} from '../localeNamespaces';

type PluginHook = (this: { addWatchFile(file: string): void }, id: string) => unknown;

const temporaryDirectories: string[] = [];

function hook(plugin: Plugin, name: 'resolveId' | 'load'): PluginHook {
  const candidate = plugin[name];
  if (typeof candidate !== 'function') throw new Error(`Missing ${name} hook`);
  return candidate as unknown as PluginHook;
}

function moduleJsonFrom(source: unknown): Record<string, unknown> {
  expect(source).toEqual(expect.any(String));
  const moduleSource = source as string;
  expect(moduleSource).toMatch(/^export default .*;$/s);
  return JSON.parse(moduleSource.slice('export default '.length, -1)) as Record<string, unknown>;
}

function fixture(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zenstory-locales-'));
  temporaryDirectories.push(root);
  for (const language of ['zh', 'en']) fs.mkdirSync(path.join(root, language));
  return root;
}

function writeLocale(root: string, language: string, namespace: string, value: unknown): string {
  const file = path.join(root, language, `${namespace}.json`);
  fs.writeFileSync(file, JSON.stringify(value));
  return file;
}

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe('locale bundle Vite plugin', () => {
  it('partitions every real locale JSON into disjoint core and lazy namespaces', () => {
    const coreSet = new Set<string>(CORE_LOCALE_NAMESPACES);
    const lazySet = new Set<string>(LAZY_LOCALE_NAMESPACES);

    for (const ns of lazySet) {
      expect(coreSet.has(ns)).toBe(false);
    }

    const expectedAll = [...CORE_LOCALE_NAMESPACES, ...LAZY_LOCALE_NAMESPACES].sort();
    for (const language of LOCALE_LANGUAGES) {
      const localeDir = path.resolve(`public/locales/${language}`);
      const realNamespaces = fs
        .readdirSync(localeDir)
        .filter((name) => name.endsWith('.json'))
        .map((name) => path.basename(name, '.json'))
        .sort();
      expect(realNamespaces).toEqual(expectedAll);
    }
  });

  it('aggregates only core namespaces in the core bundle and excludes lazy namespaces', () => {
    const plugin = localeBundlePlugin();
    const load = hook(plugin, 'load');

    for (const language of LOCALE_LANGUAGES) {
      const watched: string[] = [];
      const source = load.call(
        { addWatchFile: (file) => watched.push(file) },
        `\0virtual:zenstory-locale-${language}-core`,
      );
      const bundle = moduleJsonFrom(source);
      const localeDir = path.resolve(`public/locales/${language}`);
      const expectedCore = [...CORE_LOCALE_NAMESPACES].sort();

      expect(Object.keys(bundle).sort()).toEqual(expectedCore);
      for (const lazyNs of LAZY_LOCALE_NAMESPACES) {
        expect(bundle).not.toHaveProperty(lazyNs);
      }
      expect(bundle.common).toEqual(
        JSON.parse(fs.readFileSync(path.join(localeDir, 'common.json'), 'utf8')),
      );
      expect(watched.sort()).toEqual(expectedCore.map((ns) => path.join(localeDir, `${ns}.json`)));
    }
  });

  it('loads each lazy namespace as its own single-file virtual module', () => {
    const plugin = localeBundlePlugin();
    const load = hook(plugin, 'load');

    for (const language of LOCALE_LANGUAGES) {
      for (const lazyNs of LAZY_LOCALE_NAMESPACES) {
        const watched: string[] = [];
        const source = load.call(
          { addWatchFile: (file) => watched.push(file) },
          `\0virtual:zenstory-locale-${language}-${lazyNs}`,
        );
        const expectedFile = path.resolve(`public/locales/${language}/${lazyNs}.json`);
        expect(moduleJsonFrom(source)).toEqual(JSON.parse(fs.readFileSync(expectedFile, 'utf8')));
        expect(watched).toEqual([expectedFile]);
      }
    }
  });

  it('resolves only supported core and lazy virtual module ids', () => {
    const plugin = localeBundlePlugin();
    const resolveId = hook(plugin, 'resolveId');
    const load = hook(plugin, 'load');
    const context = { addWatchFile: () => undefined };

    expect(resolveId.call(context, 'virtual:zenstory-locale-zh-core')).toBe('\0virtual:zenstory-locale-zh-core');
    expect(resolveId.call(context, 'virtual:zenstory-locale-en-core')).toBe('\0virtual:zenstory-locale-en-core');
    expect(resolveId.call(context, 'virtual:zenstory-locale-zh-admin')).toBe('\0virtual:zenstory-locale-zh-admin');
    expect(resolveId.call(context, 'virtual:zenstory-locale-en-docs')).toBe('\0virtual:zenstory-locale-en-docs');

    expect(resolveId.call(context, 'virtual:zenstory-locale-zh')).toBeNull();
    expect(resolveId.call(context, 'virtual:zenstory-locale-zh-common')).toBeNull();
    expect(resolveId.call(context, 'virtual:zenstory-locale-fr-core')).toBeNull();
    expect(resolveId.call(context, 'virtual:zenstory-locale-zh-unknown')).toBeNull();
    expect(resolveId.call(context, './ordinary-module')).toBeNull();

    expect(load.call(context, '\0virtual:zenstory-locale-fr-core')).toBeNull();
    expect(load.call(context, '\0virtual:zenstory-locale-zh-unknown')).toBeNull();
    expect(load.call(context, '/ordinary-module')).toBeNull();
  });

  it('isolates core and lazy bundle invalidation by language and namespace', () => {
    const root = fixture();
    const zhCommon = writeLocale(root, 'zh', 'common', { greeting: '你好' });
    const zhAdmin = writeLocale(root, 'zh', 'admin', { title: '后台' });
    writeLocale(root, 'en', 'common', { greeting: 'Hello' });
    writeLocale(root, 'en', 'admin', { title: 'Admin' });
    const plugin = localeBundlePlugin(root);
    const load = hook(plugin, 'load');
    const context = { addWatchFile: () => undefined };

    const zhCoreBefore = load.call(context, '\0virtual:zenstory-locale-zh-core');
    const zhAdminBefore = load.call(context, '\0virtual:zenstory-locale-zh-admin');
    const enCoreBefore = load.call(context, '\0virtual:zenstory-locale-en-core');

    fs.writeFileSync(zhAdmin, JSON.stringify({ title: '管理后台' }));
    expect(load.call(context, '\0virtual:zenstory-locale-zh-core')).toBe(zhCoreBefore);
    expect(load.call(context, '\0virtual:zenstory-locale-en-core')).toBe(enCoreBefore);
    const zhAdminAfter = load.call(context, '\0virtual:zenstory-locale-zh-admin');
    expect(zhAdminAfter).not.toBe(zhAdminBefore);
    expect(moduleJsonFrom(zhAdminAfter)).toEqual({ title: '管理后台' });

    fs.writeFileSync(zhCommon, JSON.stringify({ greeting: '您好' }));
    const zhCoreAfter = load.call(context, '\0virtual:zenstory-locale-zh-core');
    expect(zhCoreAfter).not.toBe(zhCoreBefore);
    expect(moduleJsonFrom(zhCoreAfter).common).toEqual({ greeting: '您好' });
    expect(load.call(context, '\0virtual:zenstory-locale-en-core')).toBe(enCoreBefore);
  });

  it('fails the build when a core or lazy locale file is invalid JSON', () => {
    const root = fixture();
    fs.writeFileSync(path.join(root, 'zh', 'common.json'), '{ invalid json');
    fs.writeFileSync(path.join(root, 'zh', 'admin.json'), '{ invalid json');
    const plugin = localeBundlePlugin(root);
    const context = { addWatchFile: () => undefined };

    expect(() => hook(plugin, 'load').call(context, '\0virtual:zenstory-locale-zh-core')).toThrow(SyntaxError);
    expect(() => hook(plugin, 'load').call(context, '\0virtual:zenstory-locale-zh-admin')).toThrow(SyntaxError);
  });
});

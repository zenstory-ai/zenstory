import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Plugin } from 'vite';
import { afterEach, describe, expect, it } from 'vitest';
import { localeBundlePlugin } from '../../../scripts/locale-bundle-plugin';

type PluginHook = (this: { addWatchFile(file: string): void }, id: string) => unknown;

const temporaryDirectories: string[] = [];

function hook(plugin: Plugin, name: 'resolveId' | 'load'): PluginHook {
  const candidate = plugin[name];
  if (typeof candidate !== 'function') throw new Error(`Missing ${name} hook`);
  return candidate as unknown as PluginHook;
}

function bundleFrom(source: unknown): Record<string, unknown> {
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
  it('aggregates every real namespace without changing its JSON and watches each source', () => {
    const plugin = localeBundlePlugin();
    const watched: string[] = [];
    const source = hook(plugin, 'load').call(
      { addWatchFile: (file) => watched.push(file) },
      '\0virtual:zenstory-locale-zh',
    );
    const bundle = bundleFrom(source);
    const localeDir = path.resolve('public/locales/zh');
    const files = fs.readdirSync(localeDir).filter((name) => name.endsWith('.json')).sort();

    expect(Object.keys(bundle)).toEqual(files.map((name) => path.basename(name, '.json')));
    expect(bundle.common).toEqual(JSON.parse(fs.readFileSync(path.join(localeDir, 'common.json'), 'utf8')));
    expect(bundle.docs).toEqual(JSON.parse(fs.readFileSync(path.join(localeDir, 'docs.json'), 'utf8')));
    expect(watched).toEqual(files.map((name) => path.join(localeDir, name)));
  });

  it('exposes exactly the two supported language modules', () => {
    const plugin = localeBundlePlugin();
    const resolveId = hook(plugin, 'resolveId');
    const context = { addWatchFile: () => undefined };

    expect(resolveId.call(context, 'virtual:zenstory-locale-zh')).toBe('\0virtual:zenstory-locale-zh');
    expect(resolveId.call(context, 'virtual:zenstory-locale-en')).toBe('\0virtual:zenstory-locale-en');
    expect(resolveId.call(context, 'virtual:zenstory-locale-fr')).toBeNull();
    expect(resolveId.call(context, './ordinary-module')).toBeNull();
    expect(hook(plugin, 'load').call(context, '\0virtual:zenstory-locale-fr')).toBeNull();
    expect(hook(plugin, 'load').call(context, '/ordinary-module')).toBeNull();
  });

  it('changes only the bundle whose language JSON changed', () => {
    const root = fixture();
    const zhFile = writeLocale(root, 'zh', 'common', { greeting: '你好' });
    writeLocale(root, 'en', 'common', { greeting: 'Hello' });
    const plugin = localeBundlePlugin(root);
    const load = hook(plugin, 'load');
    const context = { addWatchFile: () => undefined };
    const zhBefore = load.call(context, '\0virtual:zenstory-locale-zh');
    const enBefore = load.call(context, '\0virtual:zenstory-locale-en');

    fs.writeFileSync(zhFile, JSON.stringify({ greeting: '您好' }));
    const zhAfter = load.call(context, '\0virtual:zenstory-locale-zh');
    const enAfter = load.call(context, '\0virtual:zenstory-locale-en');

    expect(zhAfter).not.toBe(zhBefore);
    expect(bundleFrom(zhAfter).common).toEqual({ greeting: '您好' });
    expect(enAfter).toBe(enBefore);
    expect(bundleFrom(enAfter).common).toEqual({ greeting: 'Hello' });
  });

  it('fails the build when a locale file is invalid JSON', () => {
    const root = fixture();
    fs.writeFileSync(path.join(root, 'zh', 'common.json'), '{ invalid json');
    const plugin = localeBundlePlugin(root);

    expect(() => hook(plugin, 'load').call(
      { addWatchFile: () => undefined },
      '\0virtual:zenstory-locale-zh',
    )).toThrow(SyntaxError);
  });
});

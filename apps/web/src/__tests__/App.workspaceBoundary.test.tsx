import { describe, expect, it, vi } from 'vitest';

const workspaceLoads = vi.hoisted(() => [] as string[]);

vi.mock('../components/Layout', () => {
  workspaceLoads.push('Layout');
  return { Layout: () => null };
});
vi.mock('../components/sidebar/Sidebar', () => {
  workspaceLoads.push('Sidebar');
  return { Sidebar: () => null };
});
vi.mock('../components/Editor', () => {
  workspaceLoads.push('Editor');
  return { Editor: () => null };
});
vi.mock('../components/ChatPanel', () => {
  workspaceLoads.push('ChatPanel');
  return { ChatPanel: () => null };
});

describe('public App module load boundary', () => {
  it('does not evaluate the project workbench when loading the public router', async () => {
    const { default: App } = await import('../App');
    expect(App).toBeTypeOf('function');
    expect(workspaceLoads).toEqual([]);
  });
});

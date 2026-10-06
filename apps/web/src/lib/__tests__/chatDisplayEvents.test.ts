import { describe, expect, it } from 'vitest';
import { parseChatDisplayEvents } from '../chatDisplayEvents';
import type { ToolCall } from '../../types';

const timestamp = new Date('2026-10-05T10:00:00Z');
const tools: ToolCall[] = [
  { id: 'one', tool_name: 'query_files', arguments: {}, status: 'success', result: { items: [] } },
  { id: 'two', tool_name: 'edit_file', arguments: {}, status: 'error', error: 'Edit rejected' },
];
const t = (key: string, options?: Record<string, unknown>) => (options?.agent ? `${key}:${options.agent}` : key);

describe('persisted chat display sequence', () => {
  it('retains multiple tool cycles and controls, resolving the final tool state at its original position', () => {
    const events = [
      { type: 'content', content: 'First' },
      { type: 'tool_call', tool_call_index: 0 },
      { type: 'content', content: 'Second' },
      { type: 'handoff', data: { target_agent: 'writer', reason: 'Write' } },
      { type: 'agent_selected', data: { agent_type: 'writer', agent_name: 'Writer', iteration: 2, max_iterations: 4 } },
      { type: 'tool_call', tool_call_index: 1 },
      { type: 'content', content: 'Third' },
      { type: 'workflow_stopped', data: { reason: 'clarification_needed', question: 'Which ending?' } },
    ];
    const items = parseChatDisplayEvents(JSON.stringify({ display_events: events }), tools, timestamp, t)!;
    expect(items.map(item => item.type)).toEqual(['content', 'tool_calls', 'content', 'thinking_status', 'agent_selected', 'tool_calls', 'content', 'workflow_stopped']);
    expect(items[1].toolCalls).toEqual([tools[0]]);
    expect(items[5].toolCalls).toEqual([tools[1]]);
    expect(items[3].content).toBe('chat:workflow.handoffMessage:chat:workflow.agents.writer');
    expect(items[4].iteration).toBe(2);
    expect(items[7].question).toBe('Which ending?');
    expect(new Set(items.map(item => item.id)).size).toBe(events.length);
  });

  it('shows handoffs with the role name and drops handoffs to unknown internal ids', () => {
    const events = [
      { type: 'handoff', data: { target_agent: 'quality_reviewer', reason: '' } },
      { type: 'handoff', data: { target_agent: 'mystery_agent', reason: 'Hidden' } },
    ];
    const items = parseChatDisplayEvents(JSON.stringify({ display_events: events }), tools, timestamp, t)!;
    expect(items).toHaveLength(1);
    expect(items[0].content).toBe('chat:workflow.handoffMessageShort:chat:workflow.agents.quality_reviewer');
    expect(items[0].content).not.toContain('mystery_agent');
  });

  it('maps reasoning, routing, exhaustion and completion using the same fields as live callbacks', () => {
    const events = [
      { type: 'thinking_content', content: 'Reasoning' },
      { type: 'router_thinking', data: { message: 'Choosing an agent' } },
      { type: 'router_decided', data: { initial_agent: 'planner', workflow_plan: 'Plan then write', workflow_agents: ['planner', 'writer'], routing_metadata: { decision: 'explicit' } } },
      { type: 'iteration_exhausted', data: { layer: 'tool_call', iterations_used: 4, max_iterations: 4, reason: 'Limit', last_agent: 'writer' } },
      { type: 'workflow_complete', data: { reason: 'complete', agent_type: 'writer', message: 'Done', confidence: 1 } },
    ];
    const items = parseChatDisplayEvents(JSON.stringify({ display_events: events }), [], timestamp, t)!;
    expect(items.map(item => item.type)).toEqual(events.map(event => event.type));
    expect(items[0].content).toBe('Reasoning');
    expect(items[1].content).toBe('Choosing an agent');
    expect(items[2]).toMatchObject({ initialAgent: 'planner', workflowPlan: 'Plan then write', workflowAgents: ['planner', 'writer'], routingMetadata: { decision: 'explicit' } });
    expect(items[3]).toMatchObject({ layer: 'tool_call', iterationsUsed: 4, maxIterations: 4, lastAgent: 'writer' });
    expect(items[4]).toMatchObject({ reason: 'complete', agentType: 'writer', message: 'Done', confidence: 1 });
  });

  it.each([
    null, '{', '{}', 'null', '{"display_events":[null]}', '{"display_events":[{"type":"agent_selected"}]}', '{"display_events":[]}',
    JSON.stringify({ display_events: [{ type: 'tool_call', tool_call_index: 9 }] }),
    JSON.stringify({ display_events: [{ type: 'tool_call', tool_call_index: -1 }] }),
    JSON.stringify({ display_events: [{ type: 'content', content: null }] }),
    JSON.stringify({ display_events: [{ type: 'future-event', data: {} }] }),
  ])('retains legacy rendering when original sequence is absent or invalid: %s', metadata => {
    expect(parseChatDisplayEvents(metadata, tools, timestamp, t)).toBeUndefined();
  });
});

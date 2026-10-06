import type { StreamRenderItem } from '../hooks/useChatStreaming';
import type { ToolCall } from '../types';
import { formatHandoffMessage } from './agentDisplayName';

/** Replay the sequence recorded by the server, not a guessed category order. */
export function parseChatDisplayEvents(
  metadata: string | null | undefined,
  tools: ToolCall[],
  timestamp: Date,
  t: (key: string, options?: Record<string, unknown>) => string,
): StreamRenderItem[] | undefined {
  if (!metadata) return undefined;
  try {
    const events: unknown = JSON.parse(metadata).display_events;
    if (!Array.isArray(events) || events.length === 0) return undefined;
    const items: StreamRenderItem[] = [];
    for (const [index, event] of events.entries()) {
      if (!event || typeof event !== 'object') return undefined;
      const id = `history-event-${index}`;
      const base = { id, timestamp };
      if (event.type === 'content' || event.type === 'thinking_content') {
        if (typeof event.content !== 'string') return undefined;
        items.push({ ...base, type: event.type, content: event.content });
        continue;
      }
      if (event.type === 'tool_call') {
        if (!Number.isInteger(event.tool_call_index) || event.tool_call_index < 0) return undefined;
        const tool = tools[event.tool_call_index];
        if (!tool) return undefined;
        items.push({ ...base, type: 'tool_calls', toolCalls: [tool] });
        continue;
      }
      const data = event.data;
      if (!data || typeof data !== 'object') return undefined;
      switch (event.type) {
        case 'agent_selected':
          items.push({ ...base, type: 'agent_selected', agentType: data.agent_type, agentName: data.agent_name, iteration: data.iteration, maxIterations: data.max_iterations, remaining: data.remaining });
          break;
        case 'router_thinking':
          items.push({ ...base, type: 'router_thinking', content: data.message });
          break;
        case 'router_decided':
          items.push({ ...base, type: 'router_decided', initialAgent: data.initial_agent, workflowPlan: data.workflow_plan, workflowAgents: data.workflow_agents, routingMetadata: data.routing_metadata });
          break;
        case 'handoff': {
          const content = formatHandoffMessage(data, t);
          if (content) items.push({ ...base, type: 'thinking_status', content });
          break;
        }
        case 'iteration_exhausted':
          items.push({ ...base, type: 'iteration_exhausted', layer: data.layer, iterationsUsed: data.iterations_used, maxIterations: data.max_iterations, reason: data.reason, lastAgent: data.last_agent });
          break;
        case 'workflow_stopped':
        case 'workflow_complete':
          items.push({ ...base, type: event.type, reason: data.reason, agentType: data.agent_type, message: data.message, question: data.question, context: data.context, details: data.details, confidence: data.confidence, evaluation: data.evaluation });
          break;
        default:
          // Unknown/invalid timelines must not hide the original message fields.
          return undefined;
      }
    }
    return items;
  } catch {
    return undefined;
  }
}

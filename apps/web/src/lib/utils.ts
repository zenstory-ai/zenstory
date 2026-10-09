/**
 * General utility functions for the application.
 *
 * Contains helper functions for URL handling, content sanitization,
 * class name merging, and agent response processing.
 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge class names with Tailwind CSS conflict resolution.
 *
 * Combines clsx for conditional classes with tailwind-merge to
 * intelligently merge Tailwind CSS classes without conflicts.
 *
 * @param inputs - Class values to merge (strings, objects, arrays, etc.)
 * @returns The merged class string
 *
 * @example
 * cn('px-2 py-1', 'p-4') // Returns 'p-4' (tailwind-merge resolves conflict)
 * cn('base-class', condition && 'conditional-class') // Conditional classes
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/**
 * Get the base URL for the application.
 *
 * In browser environments, uses the current window origin.
 * In server-side contexts, falls back to VITE_BASE_URL environment variable
 * or a default placeholder.
 *
 * @returns The base URL string for the application
 */
export function getBaseUrl(): string {
  if (typeof window !== 'undefined') {
    // Running in browser, use current origin
    return window.location.origin;
  }
  return import.meta.env.VITE_BASE_URL || 'https://yourdomain.com';
}

/**
 * Agent control markers used for internal system communication.
 *
 * These markers should not be displayed to users as they are used
 * for state management between the agent and frontend.
 *
 * @internal
 */
const AGENT_CONTROL_MARKERS = ['[NEEDS_CLARIFICATION]', '[TASK_COMPLETE]', '[需要澄清]', '[任务完成]'];

const AGENT_CONTROL_MARKER_PATTERN = new RegExp(
  AGENT_CONTROL_MARKERS.map(escapeRegExp).join('|'),
  'g',
);

/**
 * Escape special characters in a string for use in a regular expression.
 *
 * Adds backslashes before regex special characters: . * + ? ^ $ { } ( ) | [ ] \
 *
 * @param str - The string to escape
 * @returns The escaped string safe for use in RegExp patterns
 */
function escapeRegExp(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Strip orphaned </think&gt; tags and agent control markers from AI responses.
 *
 * The &lt;think&gt;...&lt;/think&gt; content is handled separately via streaming events,
 * but orphaned closing tags may remain. This function removes them along with
 * any internal control markers that shouldn't be shown to users.
 *
 * @param content - The raw AI response content to sanitize
 * @returns The cleaned content with think tags and control markers removed
 */
export function stripThinkTags(content: string): string {
  const result = content.replace(/<\/think>/gi, '');
  return stripAgentControlMarkers(result).trim();
}

/**
 * Remove agent control markers (English and Chinese aliases such as
 * `[TASK_COMPLETE]` / `[任务完成]`).
 *
 * @param content - Text that may contain control markers
 * @returns The text without control markers (not trimmed)
 */
export function stripAgentControlMarkers(content: string): string {
  return content.replace(AGENT_CONTROL_MARKER_PATTERN, '');
}

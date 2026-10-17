import { useState, useEffect, useCallback, useRef } from 'react';
import type { FileTreeNode } from '../types';
import { logger } from "../lib/logger";

let compareTitles: Intl.Collator['compare'] | undefined;

/**
 * Search result item with parent path information
 */
export interface FileSearchResult {
  id: string;
  title: string;
  fileType: string;
  parentPath: string; // e.g., "大纲 > 第一章"
  parentId: string | null;
}

/**
 * Options for the useFileSearch hook
 */
export interface UseFileSearchOptions {
  tree: FileTreeNode[];
  query: string;
  debounceMs?: number;
  maxResults?: number;
}

/**
 * Return type for the useFileSearch hook
 */
export interface UseFileSearchReturn {
  results: FileSearchResult[];
  isSearching: boolean;
  clearSearch: () => void;
}

/**
 * Flatten a tree of FileTreeNode into a flat array with parent path tracking
 * Handles circular references gracefully
 */
function flattenTree(
  nodes: FileTreeNode[],
  parentPath: string[] = [],
  visited: Set<string> = new Set()
): Array<{
  id: string;
  title: string;
  fileType: string;
  parentId: string | null;
  parentPath: string[];
}> {
  const result: Array<{
    id: string;
    title: string;
    fileType: string;
    parentId: string | null;
    parentPath: string[];
  }> = [];

  for (const node of nodes) {
    // Detect circular references
    if (visited.has(node.id)) {
      logger.warn(`Circular reference detected for node ${node.id}, skipping`);
      continue;
    }

    visited.add(node.id);

    result.push({
      id: node.id,
      title: node.title,
      fileType: node.file_type,
      parentId: node.parent_id,
      parentPath,
    });

    if (node.children && node.children.length > 0) {
      const childPath = [...parentPath, node.title];
      result.push(...flattenTree(node.children, childPath, visited));
    }
  }

  return result;
}

/**
 * Calculate match score for sorting results
 * Higher score = better match
 * - Exact match: 3
 * - Prefix match: 2
 * - Contains match: 1
 */
function getMatchScore(title: string, lowerQuery: string): number {
  const lowerTitle = title.toLowerCase();

  if (lowerTitle === lowerQuery) {
    return 3; // Exact match
  }
  if (lowerTitle.startsWith(lowerQuery)) {
    return 2; // Prefix match
  }
  if (lowerTitle.includes(lowerQuery)) {
    return 1; // Contains match
  }
  return 0; // No match
}

/**
 * Client-side file search hook with debouncing and fuzzy matching
 */
export function useFileSearch({
  tree,
  query,
  debounceMs = 300,
  maxResults = 50,
}: UseFileSearchOptions): UseFileSearchReturn {
  const [results, setResults] = useState<FileSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearSearch = useCallback(() => {
    setResults([]);
    setIsSearching(false);
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
  }, []);

  // Handle empty query separately
  useEffect(() => {
    if (!query.trim()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setResults([]);
       
      setIsSearching(false);
    }
  }, [query]);

  useEffect(() => {
    // Clear previous timeout
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }

    // Skip empty queries (handled by separate effect)
    if (!query.trim()) {
      return;
    }

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsSearching(true);

    // Debounce the search
    debounceRef.current = setTimeout(() => {
      // Flatten the tree
      const flatFiles = flattenTree(tree);
      const lowerQuery = query.toLowerCase();

      // Filter and search
      const searchResults: Array<{
        id: string;
        title: string;
        fileType: string;
        parentId: string | null;
        parentPath: string[];
        score: number;
      }> = [];

      for (const file of flatFiles) {
        // Calculate match score
        const score = getMatchScore(file.title, lowerQuery);

        if (score > 0) {
          searchResults.push({
            ...file,
            score,
          });
        }
      }

      // Sort by score (descending), then by title (ascending) for consistent ordering
      searchResults.sort((a, b) => {
        if (b.score !== a.score) {
          return b.score - a.score;
        }
        compareTitles ??= new Intl.Collator('zh-CN').compare;
        return compareTitles(a.title, b.title);
      });

      // Limit results
      const limitedResults = searchResults.slice(0, maxResults);

      // Transform to FileSearchResult format
      const formattedResults: FileSearchResult[] = limitedResults.map((file) => ({
        id: file.id,
        title: file.title,
        fileType: file.fileType,
        parentPath: file.parentPath.join(' > '),
        parentId: file.parentId,
      }));

      setResults(formattedResults);
      setIsSearching(false);
    }, debounceMs);

    // Cleanup on unmount or when query changes
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, [tree, query, debounceMs, maxResults]);

  return {
    results,
    isSearching,
    clearSearch,
  };
}

/**
 * Schema.org structured data generators for SEO optimization.
 *
 * This module provides functions to generate JSON-LD structured data objects
 * that help search engines understand website content and enable rich search
 * results (e.g., application info cards in Google search results).
 *
 * Architecture:
 * - Each function generates a specific Schema.org type (SoftwareApplication, etc.)
 * - Output is JSON-LD format for embedding in `<script type="application/ld+json">`
 * - Structured data improves SEO visibility and click-through rates
 *
 * Supported Schema Types:
 * - SoftwareApplication: Describes the ZenStory application for app stores/search
 *
 * Usage:
 * ```ts
 * import { generateSoftwareApplicationSchema } from '../lib/structured-data';
 *
 * // Generate schema for home page
 * const schema = generateSoftwareApplicationSchema(
 *   'https://zenstory.ai',
 *   'ZenStory',
 *   '专业的AI小说写作助手'
 * );
 *
 * // Embed in page head
 * const script = document.createElement('script');
 * script.type = 'application/ld+json';
 * script.textContent = JSON.stringify(schema);
 * document.head.appendChild(script);
 * ```
 *
 * @see https://schema.org/SoftwareApplication
 * @module lib/structured-data
 */

/**
 * Generates a Schema.org SoftwareApplication structured data object.
 *
 * Creates JSON-LD for describing a software application to search engines.
 * Used for the home page to help Google understand the product and display
 * rich results with application information.
 *
 * Generated Schema Properties:
 * - `@context`: Schema.org namespace
 * - `@type`: SoftwareApplication
 * - `name`: Application name
 * - `description`: Application description
 * - `applicationCategory`: BusinessApplication
 * - `operatingSystem`: Web (browser-based)
 * - `offers`: Free tier pricing info
 * - `url`: Application URL
 *
 * @param url - The canonical URL of the application
 * @param name - Application display name (e.g., "ZenStory")
 * @param description - Brief description of the application's features
 * @returns SoftwareApplication JSON-LD object ready for embedding
 *
 * @example
 * ```ts
 * // Chinese variant
 * const zhSchema = generateSoftwareApplicationSchema(
 *   'https://zenstory.ai',
 *   'ZenStory',
 *   '专业的AI小说写作助手，提供智能大纲生成、角色管理、世界观构建等功能'
 * );
 *
 * // English variant
 * const enSchema = generateSoftwareApplicationSchema(
 *   'https://zenstory.ai',
 *   'ZenStory',
 *   'Professional AI novel writing assistant'
 * );
 *
 * // Output structure:
 * // {
 * //   "@context": "https://schema.org",
 * //   "@type": "SoftwareApplication",
 * //   "name": "ZenStory",
 * //   "description": "...",
 * //   "applicationCategory": "BusinessApplication",
 * //   "operatingSystem": "Web",
 * //   "offers": {
 * //     "@type": "Offer",
 * //     "price": "0",
 * //     "priceCurrency": "USD"
 * //   },
 * //   "url": "https://zenstory.ai"
 * // }
 * ```
 */
export function generateSoftwareApplicationSchema(url: string, name: string, description: string): object {
  return {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: name,
    description: description,
    applicationCategory: 'BusinessApplication',
    operatingSystem: 'Web',
    offers: {
      '@type': 'Offer',
      price: '0',
      priceCurrency: 'USD',
    },
    url: url,
  };
}

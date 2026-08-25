/**
 * @fileoverview Logo components - Brand identity SVG components for zenstory application.
 *
 * This module provides logo components for displaying the zenstory brand identity
 * throughout the application. It includes both the full logo (icon + text) and
 * the icon-only version (logo mark) for different UI contexts.
 *
 * Features:
 * - Full logo with icon and "zenstory" text for prominent brand display
 * - Logo mark (icon-only) for compact spaces and favicons
 * - SVG-based for perfect scaling at any size
 * - Uses currentColor for automatic theme adaptation
 * - Customizable via className prop
 *
 * @module components/Logo
 */

/**
 * Props for logo components.
 *
 * @interface LogoProps
 */
export interface LogoProps {
  /**
   * Additional CSS classes to apply to the SVG element.
   * Commonly used for sizing (e.g., "h-7 w-auto", "h-8 w-8").
   */
  className?: string;
}


const ZENSTORY_WORDMARK_PATH = "M0.53 16.64L0.53 14.74L7.26 14.74L7.26 16.37L3.60 20.10L7.49 20.10L7.49 22L0.25 22L0.25 20.20L3.73 16.64L0.53 16.64ZM16.69 18.75L16.69 19.06L11.11 19.06Q11.18 19.73 11.47 20.06L11.47 20.06Q11.87 20.53 12.52 20.53L12.52 20.53Q12.93 20.53 13.30 20.33L13.30 20.33Q13.53 20.20 13.79 19.87L13.79 19.87L16.53 20.12Q15.90 21.21 15.01 21.69Q14.12 22.16 12.46 22.16L12.46 22.16Q11.02 22.16 10.19 21.76Q9.37 21.35 8.82 20.47Q8.28 19.58 8.28 18.38L8.28 18.38Q8.28 16.68 9.37 15.63Q10.46 14.58 12.38 14.58L12.38 14.58Q13.94 14.58 14.84 15.05Q15.74 15.52 16.21 16.42Q16.69 17.31 16.69 18.75L16.69 18.75ZM11.12 17.73L13.86 17.73Q13.77 16.92 13.42 16.57Q13.07 16.22 12.50 16.22L12.50 16.22Q11.83 16.22 11.44 16.75L11.44 16.75Q11.18 17.08 11.12 17.73L11.12 17.73ZM17.96 22L17.96 14.74L20.56 14.74L20.56 15.92Q21.14 15.20 21.73 14.89Q22.33 14.58 23.18 14.58L23.18 14.58Q24.34 14.58 24.99 15.26Q25.64 15.95 25.64 17.39L25.64 17.39L25.64 22L22.85 22L22.85 18.01Q22.85 17.32 22.59 17.04Q22.34 16.76 21.88 16.76L21.88 16.76Q21.38 16.76 21.06 17.14Q20.75 17.52 20.75 18.51L20.75 18.51L20.75 22L17.96 22ZM26.81 19.99L26.81 19.99L29.57 19.73Q29.74 20.22 30.05 20.43Q30.36 20.65 30.87 20.65L30.87 20.65Q31.43 20.65 31.74 20.41L31.74 20.41Q31.98 20.23 31.98 19.96L31.98 19.96Q31.98 19.66 31.66 19.50L31.66 19.50Q31.44 19.38 30.47 19.21L30.47 19.21Q29.02 18.96 28.45 18.74Q27.89 18.53 27.50 18.01Q27.12 17.50 27.12 16.85L27.12 16.85Q27.12 16.13 27.54 15.61Q27.95 15.09 28.68 14.83Q29.42 14.58 30.65 14.58L30.65 14.58Q31.94 14.58 32.56 14.77Q33.18 14.97 33.60 15.39Q34.01 15.81 34.28 16.52L34.28 16.52L31.64 16.78Q31.54 16.43 31.30 16.26L31.30 16.26Q30.97 16.05 30.51 16.05L30.51 16.05Q30.04 16.05 29.82 16.21Q29.61 16.38 29.61 16.62L29.61 16.62Q29.61 16.89 29.88 17.02L29.88 17.02Q30.15 17.16 31.07 17.27L31.07 17.27Q32.46 17.43 33.13 17.71Q33.81 17.99 34.17 18.51Q34.53 19.03 34.53 19.65L34.53 19.65Q34.53 20.28 34.15 20.87Q33.77 21.47 32.95 21.82Q32.14 22.17 30.73 22.17L30.73 22.17Q28.74 22.17 27.89 21.60Q27.05 21.04 26.81 19.99ZM36.42 13.41L39.21 11.98L39.21 14.74L40.74 14.74L40.74 16.77L39.21 16.77L39.21 19.35Q39.21 19.81 39.30 19.96L39.30 19.96Q39.44 20.20 39.78 20.20L39.78 20.20Q40.09 20.20 40.64 20.02L40.64 20.02L40.84 21.94Q39.81 22.16 38.92 22.16L38.92 22.16Q37.88 22.16 37.39 21.90Q36.89 21.63 36.66 21.09Q36.42 20.54 36.42 19.33L36.42 19.33L36.42 16.77L35.40 16.77L35.40 14.74L36.42 14.74L36.42 13.41ZM41.73 18.39L41.73 18.39Q41.73 16.73 42.85 15.65Q43.97 14.58 45.88 14.58L45.88 14.58Q48.06 14.58 49.17 15.84L49.17 15.84Q50.07 16.86 50.07 18.35L50.07 18.35Q50.07 20.02 48.96 21.09Q47.84 22.16 45.88 22.16L45.88 22.16Q44.13 22.16 43.05 21.28L43.05 21.28Q41.73 20.17 41.73 18.39ZM44.52 18.38L44.52 18.38Q44.52 19.35 44.91 19.82Q45.30 20.28 45.90 20.28L45.90 20.28Q46.50 20.28 46.88 19.83Q47.27 19.37 47.27 18.36L47.27 18.36Q47.27 17.41 46.88 16.95Q46.49 16.49 45.92 16.49L45.92 16.49Q45.31 16.49 44.91 16.96Q44.52 17.43 44.52 18.38ZM51.43 22L51.43 14.74L54.04 14.74L54.04 15.93Q54.41 15.16 54.81 14.87Q55.21 14.58 55.80 14.58L55.80 14.58Q56.42 14.58 57.15 14.96L57.15 14.96L56.29 16.94Q55.79 16.74 55.51 16.74L55.51 16.74Q54.96 16.74 54.66 17.19L54.66 17.19Q54.23 17.82 54.23 19.57L54.23 19.57L54.23 22L51.43 22ZM59.86 22L56.81 14.74L59.75 14.74L61.25 19.57L62.64 14.74L65.38 14.74L62.50 22.50Q62.01 23.83 61.50 24.29L61.50 24.29Q60.77 24.95 59.29 24.95L59.29 24.95Q58.69 24.95 57.43 24.78L57.43 24.78L57.21 22.84Q57.81 23.03 58.55 23.03L58.55 23.03Q59.04 23.03 59.35 22.81Q59.65 22.58 59.86 22L59.86 22Z";


/** Product mark derived from the ZenStory AI open-story identity. */
function ZenStoryMark() {
  return (
    <>
      <g fill="currentColor">
        <path d="M4.5 8.4c4.6-.9 8.5.4 11.8 4.2-4.4-.3-8.2 1.2-11.7 4.4l-.1-8.6Z" />
        <path d="M16.3 12.6c3.7-1.8 7.6-2.5 11.2-2.5v13.8c-3.7-.7-7.8.2-11.7 3.2 1.6-5.8 1.7-10.6.5-14.5Z" />
        <path d="M16.3 12.6c-3.5 2.7-6.5 6.2-8.2 11.3 2.7-2.2 5.5-3.3 8.5-3.2 1.8.1 3.4.7 4.9 1.8-1.5-3.7-3.1-7-5.2-9.9Z" />
      </g>
      <path
        d="M24.5 2.7c.7 2.7 1.8 3.8 4.5 4.5-2.7.7-3.8 1.8-4.5 4.5-.7-2.7-1.8-3.8-4.5-4.5 2.7-.7 3.8-1.8 4.5-4.5Z"
        fill="#22D3EE"
      />
    </>
  );
}

/**
 * Full zenstory logo component with icon and text.
 *
 * Renders the complete brand logo consisting of the open-story mark and the
 * "zenstory" wordmark. Uses SVG for crisp rendering
 * at any size and currentColor for theme-aware coloring.
 *
 * Design elements:
 * - Open-story forms: represent ideas becoming a structured work
 * - Cyan light: shared identity detail with the ZenStory AI organization
 * - "zenstory" text: Brand name in custom letterform design
 *
 * @param props - Component props
 * @param props.className - CSS classes for sizing/styling (default: "h-7 w-auto")
 * @returns The rendered full logo SVG component
 *
 * @example
 * // Default size in header
 * <Logo />
 *
 * @example
 * // Larger logo for login page
 * <Logo className="h-10 w-auto" />
 *
 * @example
 * // Custom color via parent's text color
 * <div className="text-blue-500">
 *   <Logo />
 * </div>
 */
export function Logo({ className = "h-7 w-auto" }: LogoProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 120 32"
      fill="none"
      className={className}
    >
      <ZenStoryMark />

      {/* 文字 - zenstory（沿用旧 zenstory 的路径字形思路，统一跨端展示） */}
      <g fill="currentColor" transform="translate(36 -1)">
        <path d={ZENSTORY_WORDMARK_PATH} />
      </g>

    </svg>
  );
}

/**
 * Logo mark component (icon-only version).
 *
 * Renders the zenstory brand icon without text, suitable for compact spaces,
 * favicons, app icons, and places where the full logo would be too large.
 *
 * Design elements:
 * - Open-story forms derived from the ZenStory AI organization mark
 * - Cyan four-point light shared across the brand family
 * - Square aspect ratio (32x32): Perfect for icon use cases
 *
 * @param props - Component props
 * @param props.className - CSS classes for sizing/styling (default: "h-8 w-8")
 * @returns The rendered logo mark SVG component
 *
 * @example
 * // Default square icon
 * <LogoMark />
 *
 * @example
 * // Small icon for tight spaces
 * <LogoMark className="h-5 w-5" />
 *
 * @example
 * // As a loading placeholder
 * <div className="animate-pulse">
 *   <LogoMark className="h-12 w-12" />
 * </div>
 */
export function LogoMark({ className = "h-8 w-8" }: LogoProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      fill="none"
      className={className}
    >
      <ZenStoryMark />
    </svg>
  );
}

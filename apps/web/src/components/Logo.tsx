/**
 * @fileoverview Logo components - Brand identity SVG components for ZenStory application.
 *
 * This module provides logo components for displaying the ZenStory brand identity
 * throughout the application. It includes both the full logo (icon + text) and
 * the icon-only version (logo mark) for different UI contexts.
 *
 * Features:
 * - Full logo with icon and "ZenStory" text for prominent brand display
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


const ZENSTORY_WORDMARK_PATH = "M0.77 11.98H9.54V13.98L3.92 19.85H9.74V22H0.23V19.92L5.79 14.12H0.77ZM19.01 19.06H13.43Q13.51 19.73 13.79 20.06Q14.2 20.53 14.85 20.53Q15.26 20.53 15.63 20.33Q15.85 20.2 16.11 19.87L18.85 20.12Q18.22 21.21 17.34 21.69Q16.45 22.16 14.79 22.16Q13.34 22.16 12.52 21.76Q11.69 21.35 11.15 20.47Q10.6 19.58 10.6 18.38Q10.6 16.68 11.69 15.63Q12.78 14.58 14.7 14.58Q16.26 14.58 17.17 15.05Q18.07 15.52 18.54 16.42Q19.01 17.31 19.01 18.75ZM16.18 17.73Q16.1 16.92 15.75 16.57Q15.39 16.22 14.82 16.22Q14.16 16.22 13.76 16.75Q13.51 17.08 13.44 17.73ZM20.29 14.74H22.88V15.92Q23.46 15.2 24.06 14.89Q24.65 14.58 25.5 14.58Q26.66 14.58 27.31 15.26Q27.97 15.95 27.97 17.39V22H25.17V18.01Q25.17 17.32 24.92 17.04Q24.66 16.76 24.21 16.76Q23.7 16.76 23.39 17.14Q23.07 17.52 23.07 18.51V22H20.29ZM29.27 18.68 32.22 18.5Q32.31 19.22 32.61 19.59Q33.09 20.2 33.97 20.2Q34.64 20.2 35 19.89Q35.36 19.58 35.36 19.17Q35.36 18.78 35.01 18.47Q34.67 18.17 33.43 17.89Q31.39 17.43 30.52 16.67Q29.65 15.92 29.65 14.74Q29.65 13.97 30.1 13.28Q30.54 12.59 31.44 12.2Q32.34 11.81 33.91 11.81Q35.83 11.81 36.84 12.52Q37.84 13.24 38.04 14.79L35.12 14.97Q35 14.29 34.63 13.98Q34.25 13.67 33.6 13.67Q33.06 13.67 32.79 13.9Q32.51 14.13 32.51 14.46Q32.51 14.7 32.74 14.89Q32.96 15.09 33.78 15.26Q35.81 15.7 36.69 16.15Q37.56 16.59 37.96 17.26Q38.36 17.92 38.36 18.74Q38.36 19.7 37.83 20.52Q37.3 21.33 36.34 21.75Q35.38 22.17 33.93 22.17Q31.37 22.17 30.39 21.19Q29.4 20.2 29.27 18.68ZM43.09 11.98V14.74H44.62V16.77H43.09V19.35Q43.09 19.81 43.18 19.96Q43.32 20.2 43.66 20.2Q43.97 20.2 44.52 20.02L44.73 21.94Q43.7 22.16 42.8 22.16Q41.76 22.16 41.27 21.9Q40.78 21.63 40.54 21.09Q40.3 20.54 40.3 19.33V16.77H39.28V14.74H40.3V13.41ZM45.61 18.39Q45.61 16.73 46.73 15.65Q47.85 14.58 49.76 14.58Q51.94 14.58 53.05 15.84Q53.95 16.86 53.95 18.35Q53.95 20.02 52.84 21.09Q51.73 22.16 49.77 22.16Q48.02 22.16 46.94 21.28Q45.61 20.17 45.61 18.39ZM48.4 18.38Q48.4 19.35 48.79 19.82Q49.18 20.28 49.78 20.28Q50.38 20.28 50.77 19.83Q51.15 19.37 51.15 18.36Q51.15 17.41 50.76 16.95Q50.37 16.49 49.8 16.49Q49.19 16.49 48.79 16.96Q48.4 17.43 48.4 18.38ZM55.32 14.74H57.92V15.93Q58.3 15.16 58.7 14.87Q59.1 14.58 59.68 14.58Q60.3 14.58 61.03 14.96L60.17 16.94Q59.68 16.74 59.39 16.74Q58.84 16.74 58.54 17.19Q58.11 17.82 58.11 19.57V22H55.32ZM60.7 14.74H63.64L65.13 19.57L66.52 14.74H69.26L66.38 22.5Q65.89 23.83 65.38 24.29Q64.65 24.95 63.17 24.95Q62.57 24.95 61.31 24.78L61.09 22.84Q61.69 23.03 62.43 23.03Q62.92 23.03 63.23 22.81Q63.53 22.58 63.75 22Z";


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
 * Full ZenStory logo component with icon and text.
 *
 * Renders the complete brand logo consisting of the open-story mark and the
 * "ZenStory" wordmark. Uses SVG for crisp rendering
 * at any size and currentColor for theme-aware coloring.
 *
 * Design elements:
 * - Open-story forms: represent ideas becoming a structured work
 * - Cyan light: shared identity detail with the ZenStory AI organization
 * - "ZenStory" text: Brand name in custom letterform design
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

      {/* 文字 - ZenStory（Arial Black 14px 字形转路径，统一跨端展示） */}
      <g fill="currentColor" transform="translate(36 -1)">
        <path d={ZENSTORY_WORDMARK_PATH} />
      </g>

    </svg>
  );
}

/**
 * Logo mark component (icon-only version).
 *
 * Renders the ZenStory brand icon without text, suitable for compact spaces,
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

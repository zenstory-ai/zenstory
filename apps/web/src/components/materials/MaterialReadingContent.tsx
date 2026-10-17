import { LazyMarkdown } from '../LazyMarkdown';

// Preserve LazyMarkdown’s escaped HTML behavior; do not enable raw-HTML plugins here.
export function MaterialReadingContent({ content, className = '' }: { content: string; className?: string }) {
  return (
    <div className={`prose material-reader-prose ${className}`}>
      <LazyMarkdown>{content}</LazyMarkdown>
    </div>
  );
}

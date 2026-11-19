import { useTranslation } from 'react-i18next';
import type { FileDeletionTarget } from '../types';

interface DeletionTargetListProps {
  targets: FileDeletionTarget[];
}

export function DeletionTargetList({ targets }: DeletionTargetListProps) {
  const { t } = useTranslation('common');
  const byId = new Map(targets.map((target) => [target.id, target]));

  const targetPath = (target: FileDeletionTarget) => {
    const titles = [target.title];
    const visited = new Set([target.id]);
    let parentId = target.parent_id;
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId);
      const parent = byId.get(parentId);
      if (!parent) break;
      titles.unshift(parent.title);
      parentId = parent.parent_id;
    }
    return titles.join(' / ');
  };

  return (
    <ul
      aria-label={t('deleteScope')}
      className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-2"
    >
      {targets.map((target) => (
        <li key={target.id} className="flex items-start justify-between gap-3 rounded-md px-2 py-1.5 text-xs">
          <span className="min-w-0 break-words text-[hsl(var(--text-primary))]">{targetPath(target)}</span>
          <span className="shrink-0 text-[hsl(var(--text-secondary))]">
            {t(`fileTypes.${target.file_type}`, { defaultValue: target.file_type })}
          </span>
        </li>
      ))}
    </ul>
  );
}

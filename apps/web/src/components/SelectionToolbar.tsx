import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Quote } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAnimatedPresence } from '../hooks/useAnimatedPresence';

const VIEWPORT_MARGIN_PX = 12;
const DEFAULT_HALF_WIDTH_PX = 72;
const DEFAULT_HEIGHT_PX = 48;

interface SelectionToolbarProps {
  /** 是否可见（默认由 text 非空推导，传入后可保持挂载完成退场动画） */
  open?: boolean;
  /** 选中的文本 */
  text: string;
  /** 工具栏位置 */
  position: { x: number; y: number } | null;
  /** 添加引用回调 */
  onAdd: () => void;
  /** 关闭回调（restoreFocus=true 表示通过 Escape 从工具栏内部退回编辑器） */
  onClose: (restoreFocus?: boolean) => void;
}

const isAppleShortcutPlatform = (): boolean => {
  if (typeof navigator === 'undefined') return false;
  const uaDataPlatform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform;
  return /mac|iphone|ipad|ipod/i.test(uaDataPlatform || navigator.platform || '');
};

function clampToolbarPosition(
  position: { x: number; y: number },
  width: number,
  height: number,
) {
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1024;
  const vh = typeof window !== 'undefined' ? window.innerHeight : 768;
  const halfWidth = width > 0 ? width / 2 : DEFAULT_HALF_WIDTH_PX;
  const toolbarHeight = height > 0 ? height : DEFAULT_HEIGHT_PX;

  const minX = Math.min(vw / 2, VIEWPORT_MARGIN_PX + halfWidth);
  const maxX = Math.max(minX, vw - VIEWPORT_MARGIN_PX - halfWidth);
  const x = Math.min(Math.max(position.x, minX), maxX);

  const minY = Math.min(vh - VIEWPORT_MARGIN_PX, VIEWPORT_MARGIN_PX + toolbarHeight);
  const maxY = Math.max(minY, vh - VIEWPORT_MARGIN_PX);
  const y = Math.min(Math.max(position.y, minY), maxY);

  return { x, y };
}

export const SelectionToolbar: React.FC<SelectionToolbarProps> = ({
  open,
  text,
  position,
  onAdd,
  onClose,
}) => {
  const { t } = useTranslation('chat');
  const toolbarRef = useRef<HTMLDivElement>(null);
  const isOpen = (open ?? Boolean(text)) && Boolean(text) && Boolean(position);
  const { isMounted, isExiting } = useAnimatedPresence(isOpen, 120);
  const [lastPosition, setLastPosition] = useState<{ x: number; y: number } | null>(position);
  if (position && (lastPosition?.x !== position.x || lastPosition?.y !== position.y)) {
    setLastPosition(position);
  }
  const activePosition = position ?? lastPosition;
  const [measuredSize, setMeasuredSize] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });

  useLayoutEffect(() => {
    if (!isOpen) return;
    const el = toolbarRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 || rect.height > 0) {
      setMeasuredSize((prev) =>
        prev.width === rect.width && prev.height === rect.height
          ? prev
          : { width: rect.width, height: rect.height }
      );
    }
  }, [isOpen, text, activePosition?.x, activePosition?.y]);

  // 处理点击外部关闭
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (toolbarRef.current && !toolbarRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    // 延迟添加事件监听，避免立即触发
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside);
    }, 0);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen, onClose]);

  // 处理 Escape 键关闭（不滚动页面、不打断光标，若焦点在工具栏内则温和交还给编辑器）
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        const hadToolbarFocus = Boolean(
          toolbarRef.current && toolbarRef.current.contains(document.activeElement),
        );
        onClose(hadToolbarFocus);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const handleAdd = () => {
    onAdd();
    onClose();
  };

  if (!isMounted || !activePosition) return null;

  const clamped = clampToolbarPosition(activePosition, measuredSize.width, measuredSize.height);
  const shortcutLabel = isAppleShortcutPlatform() ? '⌘⇧Q' : 'Ctrl+Shift+Q';

  return createPortal(
    <div
      ref={toolbarRef}
      role="toolbar"
      aria-label={t('input.addToContext')}
      aria-hidden={isExiting ? true : undefined}
      inert={isExiting ? true : undefined}
      className={`fixed z-50 ${isExiting ? 'pointer-events-none' : ''}`}
      style={{
        left: clamped.x,
        top: clamped.y,
        transform: 'translate(-50%, -100%)',
      }}
    >
      <div className={`relative origin-bottom ${isExiting ? 'motion-toolbar-exit pointer-events-none' : 'motion-toolbar-enter'}`}>
        <div className="flex items-center gap-1 px-2 py-1.5 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-primary))] rounded-xl shadow-lg">
          <button
            type="button"
            onClick={handleAdd}
            className="max-md:min-h-11 flex items-center gap-1.5 px-2.5 py-1 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded-lg transition-[transform,background-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-[0.97] motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
            title={`${t('input.addToContext')} (${shortcutLabel})`}
          >
            <Quote size={14} />
            <span>{t('input.addToContext')}</span>
          </button>
        </div>
        {/* 小三角指向选中文本 */}
        <div
          className="absolute left-1/2 -translate-x-1/2 w-2 h-2 bg-[hsl(var(--bg-secondary))] border-r border-b border-[hsl(var(--border-primary))] rotate-45"
          style={{ bottom: -5 }}
        />
      </div>
    </div>,
    document.body
  );
};

import React, { useState, useRef, useEffect, useId, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { MoreHorizontal, type LucideIcon } from "lucide-react";

export interface DropdownMenuItem {
  icon: LucideIcon;
  label: string;
  onClick: () => void | Promise<void>;
  disabled?: boolean;
}

interface DropdownMenuProps {
  items: DropdownMenuItem[];
  className?: string;
  triggerTitle?: string;
  triggerAriaLabel?: string;
  triggerTestId?: string;
  menuTestId?: string;
}

/**
 * A dropdown menu component triggered by a "more" button.
 * Supports async onClick handlers with loading states.
 */
export const DropdownMenu: React.FC<DropdownMenuProps> = ({
  items,
  className = "",
  triggerTitle = "More options",
  triggerAriaLabel,
  triggerTestId,
  menuTestId,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [loadingIndex, setLoadingIndex] = useState<number | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const menuId = useId();

  const getEnabledItemIndexes = () =>
    items
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => !item.disabled && loadingIndex === null)
      .map(({ index }) => index);

  const focusItemByIndex = (index: number | undefined) => {
    if (typeof index !== "number") return;
    requestAnimationFrame(() => {
      itemRefs.current[index]?.focus();
    });
  };

  const closeMenu = (returnFocus = false) => {
    setIsOpen(false);
    if (returnFocus) {
      requestAnimationFrame(() => {
        triggerRef.current?.focus();
      });
    }
  };

  const openMenu = (focusTarget: "first" | "last" = "first") => {
    setIsOpen(true);
    const enabledItemIndexes = getEnabledItemIndexes();
    if (enabledItemIndexes.length === 0) return;
    const targetIndex =
      focusTarget === "first"
        ? enabledItemIndexes[0]
        : enabledItemIndexes[enabledItemIndexes.length - 1];
    focusItemByIndex(targetIndex);
  };

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  // Close menu on Escape key
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsOpen(false);
        triggerRef.current?.focus();
      }
    };

    if (isOpen) {
      document.addEventListener("keydown", handleEscape);
    }

    return () => {
      document.removeEventListener("keydown", handleEscape);
    };
  }, [isOpen]);

  useEffect(() => {
    itemRefs.current = itemRefs.current.slice(0, items.length);
  }, [items.length]);

  const handleTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        openMenu("first");
        break;
      case "ArrowUp":
        event.preventDefault();
        openMenu("last");
        break;
      case "Enter":
      case " ":
        if (!isOpen) {
          event.preventDefault();
          openMenu("first");
        }
        break;
      case "Escape":
        if (isOpen) {
          event.preventDefault();
          closeMenu();
        }
        break;
      default:
        break;
    }
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const enabledItemIndexes = getEnabledItemIndexes();
    if (enabledItemIndexes.length === 0) return;

    const activeElement = document.activeElement as HTMLButtonElement | null;
    const activeIndex = itemRefs.current.findIndex((itemRef) => itemRef === activeElement);
    const enabledPosition = enabledItemIndexes.indexOf(activeIndex);

    switch (event.key) {
      case "ArrowDown": {
        event.preventDefault();
        const nextPosition = enabledPosition >= 0 ? (enabledPosition + 1) % enabledItemIndexes.length : 0;
        focusItemByIndex(enabledItemIndexes[nextPosition]);
        break;
      }
      case "ArrowUp": {
        event.preventDefault();
        const nextPosition =
          enabledPosition >= 0
            ? (enabledPosition - 1 + enabledItemIndexes.length) % enabledItemIndexes.length
            : enabledItemIndexes.length - 1;
        focusItemByIndex(enabledItemIndexes[nextPosition]);
        break;
      }
      case "Home":
        event.preventDefault();
        focusItemByIndex(enabledItemIndexes[0]);
        break;
      case "End":
        event.preventDefault();
        focusItemByIndex(enabledItemIndexes[enabledItemIndexes.length - 1]);
        break;
      case "Tab":
        closeMenu();
        break;
      case "Escape":
        event.preventDefault();
        closeMenu(true);
        break;
      default:
        break;
    }
  };

  const handleItemClick = async (item: DropdownMenuItem, index: number) => {
    if (item.disabled || loadingIndex !== null) return;

    const result = item.onClick();

    // Handle async onClick
    if (result instanceof Promise) {
      setLoadingIndex(index);
      try {
        await result;
      } finally {
        setLoadingIndex(null);
      }
    }

    closeMenu(true);
  };

  return (
    <div ref={menuRef} className={`relative ${className}`}>
      {/* Trigger button */}
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        onKeyDown={handleTriggerKeyDown}
        className="min-h-[44px] min-w-[44px] p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
        title={triggerTitle}
        aria-label={triggerAriaLabel || triggerTitle}
        aria-expanded={isOpen}
        aria-haspopup="menu"
        aria-controls={menuId}
        data-testid={triggerTestId}
      >
        <MoreHorizontal size={18} />
      </button>

      {/* Dropdown menu */}
      {isOpen && (
        <div
          id={menuId}
          className="absolute right-0 top-full mt-1 w-max min-w-[160px] max-w-[calc(100vw-1rem)] bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-md shadow-lg z-50 py-1"
          role="menu"
          onKeyDown={handleMenuKeyDown}
          data-testid={menuTestId}
        >
          {items.map((item, index) => {
            const Icon = item.icon;
            const isLoading = loadingIndex === index;
            const isDisabled = item.disabled || loadingIndex !== null;

            return (
              <button
                key={index}
                ref={(el) => {
                  itemRefs.current[index] = el;
                }}
                type="button"
                onClick={() => handleItemClick(item, index)}
                disabled={isDisabled}
                aria-disabled={isDisabled}
                className="w-full min-h-[44px] flex items-center gap-2 px-3 py-2 text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-inset"
                role="menuitem"
              >
                <Icon
                  size={16}
                  className={`shrink-0 ${isLoading ? "animate-pulse" : ""}`}
                />
                <span className="min-w-0 break-words">{isLoading ? `${item.label}...` : item.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default DropdownMenu;

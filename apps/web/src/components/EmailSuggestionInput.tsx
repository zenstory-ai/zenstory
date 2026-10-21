import { useState, type InputHTMLAttributes, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { emailSuggestions } from "../lib/emailSuggestions";

// Six rows fit under the field without an inner scroll; typing the domain narrows them.
const MAX_SUGGESTIONS = 6;

interface EmailSuggestionInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type" | "id" | "role"> {
  id: string;
  value: string;
  onValueChange: (value: string) => void;
  /** Called after a suggestion is chosen, e.g. to move on to the next field. */
  onSuggestionChosen?: () => void;
}

/**
 * Email field that completes common mail domains in a list drawn under the
 * field (a native datalist pops up detached and unstyled in some browsers).
 * Combobox keyboard pattern: arrows move, Enter picks, Escape closes.
 */
export function EmailSuggestionInput({
  id,
  value,
  onValueChange,
  onSuggestionChosen,
  className = "",
  onFocus,
  onBlur,
  onKeyDown,
  ...inputProps
}: EmailSuggestionInputProps) {
  const { t } = useTranslation("auth");
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [active, setActive] = useState(-1);
  const suggestions = emailSuggestions(value).slice(0, MAX_SUGGESTIONS);
  const open = focused && !dismissed && !inputProps.disabled && suggestions.length > 0;
  const listId = `${id}-suggestions`;
  const optionId = (index: number) => `${listId}-${index}`;

  const choose = (suggestion: string) => {
    onValueChange(suggestion);
    setDismissed(true);
    setActive(-1);
    onSuggestionChosen?.();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    onKeyDown?.(event);
    if (event.defaultPrevented || suggestions.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setDismissed(false);
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => {
        const next = current + step;
        if (next < -1) return suggestions.length - 1;
        if (next >= suggestions.length) return -1;
        return next;
      });
    } else if (event.key === "Enter" && open && active >= 0) {
      event.preventDefault();
      choose(suggestions[active]);
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      setDismissed(true);
      setActive(-1);
    }
  };

  return (
    <div className="relative">
      <input
        {...inputProps}
        id={id}
        type="email"
        value={value}
        onChange={(event) => {
          onValueChange(event.target.value);
          setDismissed(false);
          setActive(-1);
        }}
        onFocus={(event) => {
          setFocused(true);
          onFocus?.(event);
        }}
        onBlur={(event) => {
          setFocused(false);
          setActive(-1);
          onBlur?.(event);
        }}
        onKeyDown={handleKeyDown}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && active >= 0 ? optionId(active) : undefined}
        className={className}
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={t("register.emailSuggestions", "常用邮箱")}
          className="absolute inset-x-0 top-full z-20 mt-1 overflow-hidden rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] py-1 shadow-lg"
        >
          {suggestions.map((suggestion, index) => {
            const at = suggestion.indexOf("@");
            return (
              <li
                key={suggestion}
                id={optionId(index)}
                role="option"
                aria-selected={index === active}
                // Keep focus in the field so the list does not close before the click lands.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(suggestion)}
                onMouseEnter={() => setActive(index)}
                className={`flex min-h-11 cursor-pointer items-center truncate px-4 text-sm text-[hsl(var(--text-primary))] ${
                  index === active ? "bg-[hsl(var(--bg-tertiary))]" : ""
                }`}
              >
                <span>{suggestion.slice(0, at)}</span>
                <span className="text-[hsl(var(--text-secondary))]">{suggestion.slice(at)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

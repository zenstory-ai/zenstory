import React from "react";

interface FormSectionProps {
  title: string;
  children: React.ReactNode;
  className?: string;
}

export function FormSection({ title, children, className = "" }: FormSectionProps) {
  return (
    <div className={`admin-surface space-y-4 p-4 sm:space-y-5 sm:p-5 ${className}`}>
      <h2 className="text-base font-semibold text-[hsl(var(--text-primary))] sm:text-lg">
        {title}
      </h2>
      {children}
    </div>
  );
}

interface FormFieldProps {
  label: string;
  required?: boolean;
  children: React.ReactNode;
  className?: string;
  htmlFor?: string;
}

export function FormField({ label, required = false, children, className = "", htmlFor }: FormFieldProps) {
  return (
    <div className={className}>
      <label htmlFor={htmlFor} className="mb-1 block text-sm font-medium text-[hsl(var(--text-primary))]">
        {label}
        {required && <span className="text-[hsl(var(--error-text))] ml-1">*</span>}
      </label>
      {children}
    </div>
  );
}

interface TouchCheckboxProps {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  className?: string;
  disabled?: boolean;
}

export function TouchCheckbox({
  id,
  checked,
  onChange,
  label,
  className = "",
  disabled = false,
}: TouchCheckboxProps) {
  return (
    <label className={`flex items-center gap-2 cursor-pointer min-h-11 ${className}`}>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        className="w-5 h-5 rounded border-[hsl(var(--separator-color))] text-[var(--admin-accent-text)] focus:ring-2 focus:ring-[hsl(var(--accent-primary))] disabled:opacity-50 disabled:cursor-not-allowed"
      />
      <span className="text-sm text-[hsl(var(--text-primary))]">{label}</span>
    </label>
  );
}

"use client";

import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Minimal, typed form primitives for the installer capture steps (task_15_03).
 * The installer keeps its own tiny set (no shadcn kit) so the throwaway UI
 * stays dependency-light. All inputs are controlled and fully typed.
 */

interface FieldProps {
  id: string;
  label: string;
  /** Inline validation error for this field, if any. */
  error?: string;
  hint?: ReactNode;
  children: ReactNode;
}

/** A labelled field wrapper that renders an error/hint line below the input. */
export function Field({ id, label, error, hint, children }: FieldProps) {
  return (
    <div className="flex flex-col gap-1.5" data-testid={`field-${id}`}>
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {error ? (
        <p data-testid={`field-error-${id}`} className="text-sm text-red-500">
          {error}
        </p>
      ) : (
        hint && <p className="text-muted-foreground text-xs">{hint}</p>
      )}
    </div>
  );
}

const inputClass = (hasError: boolean): string =>
  cn(
    "w-full rounded-md border bg-background px-3 py-2 text-sm outline-none transition-colors",
    "focus:ring-2 focus:ring-ring",
    hasError ? "border-red-500" : "border-input",
  );

interface TextInputProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Use a password input for secrets so the value isn't shoulder-surfed. */
  secret?: boolean;
  error?: boolean;
  autoComplete?: string;
}

/** A controlled text/password input. Secret inputs default to autoComplete off. */
export function TextInput({
  id,
  value,
  onChange,
  placeholder,
  secret,
  error,
  autoComplete,
}: TextInputProps) {
  return (
    <input
      id={id}
      data-testid={`input-${id}`}
      type={secret ? "password" : "text"}
      value={value}
      placeholder={placeholder}
      autoComplete={autoComplete ?? (secret ? "new-password" : "off")}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass(Boolean(error))}
    />
  );
}

interface NumberInputProps {
  id: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  error?: boolean;
}

/** A controlled number input that emits a parsed integer (NaN -> 0). */
export function NumberInput({ id, value, onChange, min, max, error }: NumberInputProps) {
  return (
    <input
      id={id}
      data-testid={`input-${id}`}
      type="number"
      value={Number.isNaN(value) ? "" : value}
      min={min}
      max={max}
      onChange={(e) => {
        const parsed = Number.parseInt(e.target.value, 10);
        onChange(Number.isNaN(parsed) ? 0 : parsed);
      }}
      className={inputClass(Boolean(error))}
    />
  );
}

interface SelectProps<T extends string> {
  id: string;
  value: T;
  onChange: (value: T) => void;
  options: ReadonlyArray<{ value: T; label: string }>;
}

/** A controlled select bound to a string-union type. */
export function Select<T extends string>({ id, value, onChange, options }: SelectProps<T>) {
  return (
    <select
      id={id}
      data-testid={`input-${id}`}
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      className={inputClass(false)}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

interface CheckboxProps {
  id: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  disabled?: boolean;
}

/** A controlled checkbox row. */
export function Checkbox({ id, checked, onChange, label, disabled }: CheckboxProps) {
  return (
    <label
      htmlFor={id}
      className={cn("flex items-center gap-2 text-sm", disabled && "cursor-not-allowed opacity-50")}
    >
      <input
        id={id}
        data-testid={`input-${id}`}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-input"
      />
      {label}
    </label>
  );
}

// src/components/ui/select.tsx
import React, { useState, useContext, createContext, ReactNode } from 'react';

interface SelectContextValue {
  value: string;
  onValueChange: (val: string) => void;
  open: boolean;
  toggle: () => void;
  close: () => void;
}
const SelectContext = createContext<SelectContextValue | null>(null);

export interface SelectProps {
  value: string;
  onValueChange: (val: string) => void;
  children?: ReactNode;      // optional now
}
export function Select({ value, onValueChange, children }: SelectProps) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen(o => !o);
  const close = () => setOpen(false);

  return (
    <SelectContext.Provider value={{ value, onValueChange, open, toggle, close }}>
      <div className="relative inline-block text-left">{children}</div>
    </SelectContext.Provider>
  );
}

export interface SelectTriggerProps {
  children?: ReactNode;      // optional now
  className?: string;
}
export function SelectTrigger({ children, className = '' }: SelectTriggerProps) {
  const ctx = useContext(SelectContext);
  if (!ctx) throw new Error("SelectTrigger must be inside a Select");
  return (
    <div
      onClick={ctx.toggle}
      className={`border p-2 rounded cursor-pointer select-none ${className}`}
    >
      {children}
    </div>
  );
}

export interface SelectValueProps {
  placeholder?: string;
}
export function SelectValue({ placeholder }: SelectValueProps) {
  const ctx = useContext(SelectContext);
  if (!ctx) throw new Error("SelectValue must be inside a Select");
  return <span>{ctx.value || placeholder}</span>;
}

export interface SelectContentProps {
  children?: ReactNode;      // optional now
  className?: string;
}
export function SelectContent({ children, className = '' }: SelectContentProps) {
  const ctx = useContext(SelectContext);
  if (!ctx) throw new Error("SelectContent must be inside a Select");
  if (!ctx.open) return null;
  return (
    <div
      className={`absolute z-10 mt-1 w-full bg-white border rounded shadow-lg ${className}`}
    >
      {children}
    </div>
  );
}

export interface SelectItemProps {
  value: string;
  children?: ReactNode;      // optional now
  className?: string;
  key?: React.Key;           // allow React key
}
export function SelectItem({
  value,
  children,
  className = ''
}: SelectItemProps) {
  const ctx = useContext(SelectContext);
  if (!ctx) throw new Error("SelectItem must be inside a SelectContent");
  const handleClick = () => {
    ctx.onValueChange(value);
    ctx.close();
  };
  const isSelected = ctx.value === value;
  return (
    <div
      onClick={handleClick}
      className={`p-2 hover:bg-gray-100 cursor-pointer ${isSelected ? 'bg-gray-200' : ''} ${className}`}
    >
      {children}
    </div>
  );
}

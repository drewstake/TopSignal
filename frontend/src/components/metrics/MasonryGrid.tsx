import type { ReactNode } from "react";

import { cn } from "../ui/cn";

interface MasonryGridProps {
  children: ReactNode;
  className?: string;
}

export function MasonryGrid({ children, className }: MasonryGridProps) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-3 auto-rows-[minmax(120px,auto)] sm:grid-cols-2 md:grid-cols-6 lg:grid-cols-12",
        className,
      )}
    >
      {children}
    </div>
  );
}


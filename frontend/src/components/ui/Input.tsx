import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ className, ...props }, ref) {
  return (
    <input
      ref={ref}
      className={cn(
        "block h-11 min-w-0 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground placeholder:text-muted focus:border-accent focus:ring-2 focus:ring-accent/40 focus:ring-offset-0",
        className,
      )}
      {...props}
    />
  );
});

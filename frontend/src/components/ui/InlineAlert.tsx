import type { HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

type AlertVariant = "info" | "success" | "warning" | "error";

const variantClasses: Record<AlertVariant, string> = {
  info: "border-accent/35 bg-accent/10 text-accentSoft",
  success: "border-success/35 bg-success/10 text-success",
  warning: "border-warning/35 bg-warning/10 text-warning",
  error: "border-danger/35 bg-danger/10 text-danger",
};

type InlineAlertProps = HTMLAttributes<HTMLDivElement> & {
  variant?: AlertVariant;
};

export function InlineAlert({ variant = "info", className, ...props }: InlineAlertProps) {
  return (
    <div
      role="alert"
      className={cn("rounded-xl2 border px-3.5 py-2.5 text-sm font-medium", variantClasses[variant], className)}
      {...props}
    />
  );
}

import type { HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

type CardVariant = "base" | "elevated" | "metric";

const variantClasses: Record<CardVariant, string> = {
  base: "glass-panel shadow-panel",
  elevated: "glass-panel shadow-neon",
  metric: "glass-panel shadow-panel border-accent/25",
};

type CardProps = HTMLAttributes<HTMLDivElement> & {
  variant?: CardVariant;
};

export function Card({ className, variant = "base", ...props }: CardProps) {
  return <div className={cn("rounded-2xl p-4 sm:p-5", variantClasses[variant], className)} {...props} />;
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-lg font-semibold text-foreground", className)} {...props} />;
}

export function CardDescription({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("mt-1 text-sm text-muted", className)} {...props} />;
}

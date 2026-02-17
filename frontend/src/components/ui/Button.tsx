import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

type ButtonVariantOptions = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  fullWidth?: boolean;
  className?: string;
};

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-slate-950 shadow-neon hover:bg-accentSoft focus-visible:ring-accent/60 disabled:bg-accent/40",
  secondary:
    "bg-surfaceAlt text-foreground hover:bg-surfaceAlt/80 focus-visible:ring-accent/45",
  ghost: "bg-transparent text-muted hover:bg-surfaceAlt/70 hover:text-foreground focus-visible:ring-accent/45",
  danger:
    "bg-danger/20 text-danger hover:bg-danger/30 focus-visible:ring-danger/50 disabled:bg-danger/15 disabled:text-danger/65",
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: "h-9 px-3 text-sm",
  md: "h-11 px-4 text-sm",
  lg: "h-12 px-5 text-base",
};

const sharedButtonClasses =
  "inline-flex items-center justify-center gap-2 rounded-xl2 border border-transparent font-semibold transition duration-250 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-80";

export function buttonVariants({
  variant = "primary",
  size = "md",
  fullWidth = false,
  className,
}: ButtonVariantOptions = {}) {
  return cn(sharedButtonClasses, variantClasses[variant], sizeClasses[size], fullWidth && "w-full", className);
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  fullWidth?: boolean;
  loading?: boolean;
  loadingText?: string;
};

export function Button({
  className,
  variant,
  size,
  fullWidth,
  loading = false,
  loadingText,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={buttonVariants({ variant, size, fullWidth, className })}
      disabled={disabled || loading}
      {...props}
    >
      {loading && (
        <span
          className="inline-block size-4 animate-spin rounded-full border-2 border-current border-t-transparent"
          aria-hidden="true"
        />
      )}
      <span>{loading && loadingText ? loadingText : children}</span>
    </button>
  );
}

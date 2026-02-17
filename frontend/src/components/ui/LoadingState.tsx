import { cn } from "@/lib/cn";

type LoadingStateProps = {
  label: string;
  className?: string;
};

export function LoadingState({ label, className }: LoadingStateProps) {
  return (
    <div className={cn("flex min-h-[140px] items-center justify-center rounded-2xl glass-panel p-6", className)} role="status">
      <div className="flex items-center gap-3 text-sm text-muted">
        <span className="inline-block size-5 animate-spin rounded-full border-2 border-accent/35 border-t-accent" aria-hidden="true" />
        {label}
      </div>
    </div>
  );
}

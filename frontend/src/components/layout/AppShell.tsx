import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

type AppShellProps = {
  children: ReactNode;
  className?: string;
};

export function AppShell({ children, className }: AppShellProps) {
  return (
    <div className="relative min-h-screen overflow-x-clip pb-12 mobile-safe-pb">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-gradient-to-b from-white/5 via-white/[0.02] to-transparent" />
      <div className="pointer-events-none absolute inset-0 opacity-50 grid-noise" />

      <main className={cn("relative mx-auto w-full max-w-6xl px-3 pt-5 sm:px-6 sm:pt-7 lg:px-8", className)}>{children}</main>
    </div>
  );
}

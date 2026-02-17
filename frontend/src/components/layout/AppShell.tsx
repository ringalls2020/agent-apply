import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

type AppShellProps = {
  children: ReactNode;
  className?: string;
};

export function AppShell({ children, className }: AppShellProps) {
  return (
    <div className="relative min-h-screen overflow-x-clip pb-12">
      <div className="pointer-events-none absolute -left-24 top-20 size-72 animate-float rounded-full bg-cyan-400/20 blur-3xl" />
      <div className="pointer-events-none absolute right-[-90px] top-[28%] size-80 animate-pulseSoft rounded-full bg-fuchsia-500/20 blur-3xl" />
      <div className="pointer-events-none absolute inset-0 opacity-40 grid-noise" />

      <main className={cn("relative mx-auto w-full max-w-6xl px-4 pt-7 sm:px-6 lg:px-8", className)}>{children}</main>
    </div>
  );
}

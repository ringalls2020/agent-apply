import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Card } from "@/components/ui/Card";

type AuthShellProps = {
  title: string;
  subtitle: string;
  children: ReactNode;
};

export function AuthShell({ title, subtitle, children }: AuthShellProps) {
  return (
    <AppShell className="flex min-h-screen items-center justify-center pb-6 pt-6 sm:pb-8 sm:pt-8">
      <div className="w-full max-w-lg px-0.5 sm:px-0">
        <Card variant="elevated" className="p-5 sm:p-8">
          <div className="mb-6 space-y-2 text-center">
            <p className="text-xs font-semibold uppercase tracking-[0.3em] text-accentSoft">AgentApply</p>
            <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">{title}</h1>
            <p className="text-sm text-muted text-wrap-anywhere">{subtitle}</p>
          </div>
          {children}
        </Card>
      </div>
    </AppShell>
  );
}

import Link from "next/link";

import { AppShell } from "@/components/layout/AppShell";
import { buttonVariants } from "@/components/ui/Button";
import { Card, CardDescription, CardTitle } from "@/components/ui/Card";

const pillars = [
  {
    title: "Smart Discovery",
    description: "Continuously scan relevant opportunities and rank roles against your profile goals.",
  },
  {
    title: "Autonomous Apply",
    description: "Run controlled application workflows at your chosen pace with clear safety controls.",
  },
  {
    title: "Operator Visibility",
    description: "Track contacts, statuses, and submission cadence from one unified mission dashboard.",
  },
];

export default function HomePage() {
  return (
    <AppShell className="space-y-8 pb-8 sm:space-y-12">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-accentSoft">AgentApply</p>
          <p className="text-sm text-muted text-wrap-anywhere">Autonomous job pipeline orchestration</p>
        </div>
        <div className="grid w-full grid-cols-2 gap-2 sm:w-auto sm:flex sm:items-center">
          <Link href="/login" className={buttonVariants({ variant: "ghost", size: "sm" })}>
            Login
          </Link>
          <Link href="/signup" className={buttonVariants({ variant: "primary", size: "sm" })}>
            Create account
          </Link>
        </div>
      </header>

      <section className="glass-panel rounded-3xl p-5 shadow-neon sm:p-9">
        <div className="max-w-3xl space-y-5">
          <p className="inline-flex rounded-full border border-accent/40 bg-accent/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-accentSoft text-wrap-anywhere sm:text-xs sm:tracking-[0.2em]">
            Operational command for modern job search
          </p>
          <h1 className="max-w-2xl text-balance text-4xl font-semibold leading-[1.1] sm:text-5xl">
            Launch a sophisticated, agent-driven application workflow.
          </h1>
          <p className="max-w-2xl text-base text-muted sm:text-lg">
            Configure targeting preferences, upload your resume context, and run automated apply cycles with full
            transparency into outcomes.
          </p>
          <div className="flex flex-col gap-2.5 sm:flex-row sm:flex-wrap sm:items-center sm:gap-3">
            <Link href="/signup" className={buttonVariants({ variant: "primary", size: "lg" })}>
              Start free workflow
            </Link>
            <Link href="/login" className={buttonVariants({ variant: "secondary", size: "lg" })}>
              Access dashboard
            </Link>
          </div>
        </div>
      </section>

      <section className="grid gap-3 sm:gap-4 md:grid-cols-3">
        {pillars.map((pillar) => (
          <Card key={pillar.title} className="h-full">
            <CardTitle>{pillar.title}</CardTitle>
            <CardDescription>{pillar.description}</CardDescription>
          </Card>
        ))}
      </section>
    </AppShell>
  );
}

"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

const navLinks = [
  { href: "/applications", label: "Applications" },
  { href: "/profile", label: "Profile" },
  { href: "/preferences", label: "Preferences" },
  { href: "/resume", label: "Resume" },
];

export function Nav() {
  const router = useRouter();
  const pathname = usePathname();

  return (
    <nav className="mb-6 rounded-2xl border border-border/80 bg-surface/75 p-4 shadow-panel backdrop-blur-xl">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="space-y-0.5">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-accentSoft">AgentApply Console</p>
          <p className="text-lg font-semibold text-foreground">Control center</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {navLinks.map((link) => {
            const isActive = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "rounded-xl2 px-3 py-2 text-sm font-medium transition duration-250 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                  isActive
                    ? "bg-accent/18 text-accentSoft shadow-[inset_0_0_0_1px_rgba(34,211,238,0.45)]"
                    : "text-muted hover:bg-surfaceAlt/70 hover:text-foreground",
                )}
              >
                {link.label}
              </Link>
            );
          })}

          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              localStorage.removeItem("agent_apply_token");
              document.cookie = "agent_apply_token=; Path=/; Max-Age=0; SameSite=Lax";
              router.push("/login");
            }}
          >
            Logout
          </Button>
        </div>
      </div>
    </nav>
  );
}

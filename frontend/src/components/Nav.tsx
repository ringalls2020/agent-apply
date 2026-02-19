"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { clearAuthToken } from "@/lib/authToken";
import { cn } from "@/lib/cn";

const navLinks = [
  { href: "/applications", label: "Applications" },
  { href: "/profile", label: "Profile" },
  { href: "/preferences", label: "Preferences" },
];

export function Nav() {
  const router = useRouter();
  const pathname = usePathname();

  const handleLogout = () => {
    clearAuthToken();
    router.push("/login");
  };

  return (
    <nav className="mb-5 rounded-2xl border border-border/80 bg-surface/75 p-3.5 shadow-panel backdrop-blur-xl sm:mb-6 sm:p-4">
      <div className="flex flex-col gap-3.5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-0.5">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-accentSoft">AgentApply Console</p>
            <p className="text-lg font-semibold text-foreground sm:text-xl">Control center</p>
          </div>
          <Button variant="secondary" size="sm" className="hidden md:inline-flex" onClick={handleLogout}>
            Logout
          </Button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {navLinks.map((link) => {
            const isActive = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "inline-flex min-h-10 items-center rounded-xl2 px-2.5 py-2 text-sm font-medium transition duration-250 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:px-3",
                  isActive
                    ? "bg-accent/16 text-foreground shadow-[inset_0_0_0_1px_rgba(212,212,212,0.28)] ring-1 ring-accent/30"
                    : "text-muted hover:bg-surfaceAlt/70 hover:text-foreground",
                )}
              >
                {link.label}
              </Link>
            );
          })}
        </div>

        <Button variant="secondary" size="sm" fullWidth className="md:hidden" onClick={handleLogout}>
          Logout
        </Button>
      </div>
    </nav>
  );
}

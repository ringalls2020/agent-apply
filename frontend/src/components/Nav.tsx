"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

export function Nav() {
  const router = useRouter();

  return (
    <div className="nav card">
      <div>
        <strong>AgentApply</strong>
      </div>
      <div style={{ display: "flex", gap: 12 }}>
        <Link href="/applications">Applications</Link>
        <Link href="/preferences">Preferences</Link>
        <Link href="/resume">Resume</Link>
        <button
          onClick={() => {
            localStorage.removeItem("agent_apply_token");
            router.push("/login");
          }}
        >
          Logout
        </button>
      </div>
    </div>
  );
}

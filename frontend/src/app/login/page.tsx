"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation } from "@apollo/client";

import { AuthShell } from "@/components/layout/AuthShell";
import { Button } from "@/components/ui/Button";
import { FormField } from "@/components/ui/FormField";
import { InlineAlert } from "@/components/ui/InlineAlert";
import { setAuthToken } from "@/lib/authToken";
import { LOGIN } from "@/graphql/operations";

function LoginInner() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const router = useRouter();
  const [login, { loading }] = useMutation(LOGIN);

  return (
    <AuthShell
      title="Welcome back"
      subtitle="Resume your autonomous application pipeline and monitor progress."
    >
      <form
        className="space-y-3.5 sm:space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            const result = await login({ variables: { email, password } });
            const token = result.data?.login?.token;
            if (!token) {
              setError("Could not login.");
              return;
            }
            setAuthToken(token);
            router.push("/applications");
          } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Could not login.");
          }
        }}
      >
        <FormField
          id="login-email"
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <FormField
          id="login-password"
          label="Password"
          type="password"
          autoComplete="current-password"
          placeholder="Enter your password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />

        {error && <InlineAlert variant="error">{error}</InlineAlert>}

        <Button type="submit" fullWidth loading={loading} loadingText="Logging in...">
          Login
        </Button>

        <p className="text-center text-sm text-muted text-wrap-anywhere">
          No account yet?{" "}
          <Link href="/signup" className="font-semibold text-accentSoft hover:text-accent">
            Create one
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}

export default function LoginPage() {
  return <LoginInner />;
}

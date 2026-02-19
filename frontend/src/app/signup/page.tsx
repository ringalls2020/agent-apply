"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation } from "@apollo/client";

import { AuthShell } from "@/components/layout/AuthShell";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/Button";
import { FormField } from "@/components/ui/FormField";
import { InlineAlert } from "@/components/ui/InlineAlert";
import { LoadingState } from "@/components/ui/LoadingState";
import { setAuthToken } from "@/lib/authToken";
import { SIGNUP } from "@/graphql/operations";
import { useRedirectAuthenticatedUser } from "@/lib/useRedirectAuthenticatedUser";

function SignupInner() {
  const { isCheckingSession } = useRedirectAuthenticatedUser();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const router = useRouter();
  const [signup, { loading }] = useMutation(SIGNUP);

  if (isCheckingSession) {
    return (
      <AppShell>
        <LoadingState label="Checking session..." />
      </AppShell>
    );
  }

  return (
    <AuthShell
      title="Create your account"
      subtitle="Set up your profile and start automating qualified job applications."
    >
      <form
        className="space-y-3.5 sm:space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            const result = await signup({ variables: { fullName: name, email, password } });
            const token = result.data?.signup?.token;
            if (!token) {
              setError("Could not sign up.");
              return;
            }
            setAuthToken(token);
            router.push("/applications");
          } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Could not sign up.");
          }
        }}
      >
        <FormField
          id="signup-name"
          label="Full name"
          placeholder="Alex Morgan"
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <FormField
          id="signup-email"
          label="Email"
          type="email"
          placeholder="you@example.com"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <FormField
          id="signup-password"
          label="Password"
          type="password"
          placeholder="Choose a secure password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />

        {error && <InlineAlert variant="error">{error}</InlineAlert>}

        <Button type="submit" fullWidth loading={loading} loadingText="Creating account...">
          Sign up
        </Button>

        <p className="text-center text-sm text-muted text-wrap-anywhere">
          Already registered?{" "}
          <Link href="/login" className="font-semibold text-accentSoft hover:text-accent">
            Login
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}

export default function SignupPage() {
  return <SignupInner />;
}

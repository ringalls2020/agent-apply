"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ApolloProvider, useMutation } from "@apollo/client";

import { getClient } from "@/lib/apollo";
import { SIGNUP } from "@/graphql/operations";

function SignupInner() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const router = useRouter();
  const [signup, { loading }] = useMutation(SIGNUP);

  return (
    <div className="card" style={{ maxWidth: 520, margin: "0 auto" }}>
      <h2>Create your account</h2>
      <label>Name</label>
      <input value={name} onChange={(e) => setName(e.target.value)} />
      <label>Email</label>
      <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" />
      <label>Password</label>
      <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" />
      {error && <p style={{ color: "#fca5a5" }}>{error}</p>}
      <button
        disabled={loading}
        onClick={async () => {
          setError("");
          try {
            const result = await signup({ variables: { name, email, password } });
            const token = result.data.signup.token;
            localStorage.setItem("agent_apply_token", token);
            document.cookie = `agent_apply_token=${encodeURIComponent(token)}; Path=/; SameSite=Lax`;
            router.push("/applications");
          } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Could not sign up.");
          }
        }}
      >
        {loading ? "Creating..." : "Sign up"}
      </button>
    </div>
  );
}

export default function SignupPage() {
  return (
    <ApolloProvider client={getClient()}>
      <SignupInner />
    </ApolloProvider>
  );
}

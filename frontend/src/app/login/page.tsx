"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ApolloProvider, useMutation } from "@apollo/client";

import { getClient } from "@/lib/apollo";
import { LOGIN } from "@/graphql/operations";

function LoginInner() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const router = useRouter();
  const [login, { loading }] = useMutation(LOGIN);

  return (
    <div className="card" style={{ maxWidth: 520, margin: "0 auto" }}>
      <h2>Welcome back</h2>
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
            const result = await login({ variables: { email, password } });
            const token = result.data.login.token;
            localStorage.setItem("agent_apply_token", token);
            document.cookie = `agent_apply_token=${encodeURIComponent(token)}; Path=/; SameSite=Lax`;
            router.push("/applications");
          } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Could not login.");
          }
        }}
      >
        {loading ? "Logging in..." : "Login"}
      </button>
    </div>
  );
}

export default function LoginPage() {
  return (
    <ApolloProvider client={getClient()}>
      <LoginInner />
    </ApolloProvider>
  );
}

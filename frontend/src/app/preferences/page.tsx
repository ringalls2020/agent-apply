"use client";

import { ApolloProvider, useMutation, useQuery } from "@apollo/client";
import { useEffect, useState } from "react";

import { Nav } from "@/components/Nav";
import { ME, UPDATE_PREFERENCES } from "@/graphql/operations";
import { getClient } from "@/lib/apollo";
import { useRequireAuth } from "@/lib/useRequireAuth";

function PreferencesInner() {
  const { isCheckingAuth, isAuthenticated } = useRequireAuth();
  const { data, refetch } = useQuery(ME, { skip: !isAuthenticated });
  const [savePreferences, { loading }] = useMutation(UPDATE_PREFERENCES);
  const [interests, setInterests] = useState("ai,automation");
  const [applicationsPerDay, setApplicationsPerDay] = useState(3);
  const [error, setError] = useState("");

  useEffect(() => {
    if (data?.me) {
      setInterests(data.me.interests.join(","));
      setApplicationsPerDay(data.me.applicationsPerDay);
    }
  }, [data]);

  if (isCheckingAuth) return <p>Checking session...</p>;
  if (!isAuthenticated) return <p>Redirecting to login...</p>;

  return (
    <>
      <Nav />
      <div className="card" style={{ maxWidth: 700 }}>
        <h2>User preferences</h2>
        <p className="small">Control which opportunities are targeted and how aggressively applications are submitted daily.</p>
        <label>Interests (comma separated)</label>
        <input value={interests} onChange={(e) => setInterests(e.target.value)} />
        <label>Applications per day</label>
        <input
          type="number"
          min={1}
          max={30}
          value={applicationsPerDay}
          onChange={(e) => setApplicationsPerDay(Number(e.target.value))}
        />
        <button
          disabled={loading}
          onClick={async () => {
            setError("");
            try {
              await savePreferences({
                variables: {
                  interests: interests.split(",").map((item) => item.trim()).filter(Boolean),
                  applicationsPerDay,
                },
              });
              await refetch();
            } catch (err: unknown) {
              setError(err instanceof Error ? err.message : "Could not save preferences.");
            }
          }}
        >
          {loading ? "Saving..." : "Save preferences"}
        </button>
        {error && <p style={{ color: "#fca5a5" }}>{error}</p>}
      </div>
    </>
  );
}

export default function PreferencesPage() {
  return (
    <ApolloProvider client={getClient()}>
      <PreferencesInner />
    </ApolloProvider>
  );
}

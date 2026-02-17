"use client";

import { ApolloProvider, useMutation, useQuery } from "@apollo/client";
import { useEffect, useState } from "react";

import { Nav } from "@/components/Nav";
import { ME, UPDATE_PREFERENCES } from "@/graphql/operations";
import { getClient } from "@/lib/apollo";

function PreferencesInner() {
  const { data, refetch } = useQuery(ME);
  const [savePreferences, { loading }] = useMutation(UPDATE_PREFERENCES);
  const [interests, setInterests] = useState("ai,automation");
  const [applicationsPerDay, setApplicationsPerDay] = useState(3);

  useEffect(() => {
    if (data?.me) {
      setInterests(data.me.interests.join(","));
      setApplicationsPerDay(data.me.applicationsPerDay);
    }
  }, [data]);

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
            await savePreferences({
              variables: {
                interests: interests.split(",").map((item) => item.trim()).filter(Boolean),
                applicationsPerDay,
              },
            });
            await refetch();
          }}
        >
          {loading ? "Saving..." : "Save preferences"}
        </button>
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

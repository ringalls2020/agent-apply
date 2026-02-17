"use client";

import { ApolloProvider, useMutation, useQuery } from "@apollo/client";

import { Nav } from "@/components/Nav";
import { APPLICATIONS, ME, RUN_AGENT } from "@/graphql/operations";
import { getClient } from "@/lib/apollo";

function ApplicationsInner() {
  const { data: meData } = useQuery(ME);
  const { data, loading, refetch } = useQuery(APPLICATIONS);
  const [runAgent, { loading: running }] = useMutation(RUN_AGENT);

  const apps = data?.applications ?? [];

  return (
    <>
      <Nav />
      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <div className="card"><h3>Applications</h3><p>{apps.length}</p></div>
        <div className="card"><h3>Daily Rate</h3><p>{meData?.me?.applicationsPerDay ?? "-"}</p></div>
        <div className="card"><h3>Interests</h3><p>{(meData?.me?.interests ?? []).join(", ") || "-"}</p></div>
      </div>

      <div className="card">
        <h2>Application review</h2>
        <p className="small">Review all applications submitted by the agent and contacts found per role.</p>
        <button
          disabled={running}
          onClick={async () => {
            await runAgent();
            await refetch();
          }}
          style={{ marginBottom: 14 }}
        >
          {running ? "Running agent..." : "Run agent now"}
        </button>

        {loading ? (
          <p>Loading applications...</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Company</th>
                <th>Role</th>
                <th>Status</th>
                <th>Point of Contact</th>
                <th>Submitted</th>
              </tr>
            </thead>
            <tbody>
              {apps.map((app: any) => (
                <tr key={app.id}>
                  <td>{app.company}</td>
                  <td>{app.title}</td>
                  <td>{app.status}</td>
                  <td>{app.contactName} ({app.contactEmail})</td>
                  <td>{new Date(app.submittedAt).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

export default function ApplicationsPage() {
  return (
    <ApolloProvider client={getClient()}>
      <ApplicationsInner />
    </ApolloProvider>
  );
}

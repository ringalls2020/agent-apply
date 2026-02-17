"use client";

import { ApolloProvider, useMutation, useQuery } from "@apollo/client";
import { useState } from "react";

import { Nav } from "@/components/Nav";
import { ME, UPLOAD_RESUME } from "@/graphql/operations";
import { getClient } from "@/lib/apollo";
import { useRequireAuth } from "@/lib/useRequireAuth";

function ResumeInner() {
  const { isCheckingAuth, isAuthenticated } = useRequireAuth();
  const [error, setError] = useState("");
  const { data, refetch } = useQuery(ME, { skip: !isAuthenticated });
  const [uploadResume, { loading }] = useMutation(UPLOAD_RESUME);

  if (isCheckingAuth) return <p>Checking session...</p>;
  if (!isAuthenticated) return <p>Redirecting to login...</p>;

  return (
    <>
      <Nav />
      <div className="card" style={{ maxWidth: 700 }}>
        <h2>Resume upload</h2>
        <p className="small">Upload your resume file so the application agent can tune matching.</p>
        <input
          type="file"
          accept=".txt,.md,.pdf,.doc,.docx"
          onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            const text = await file.text();
            setError("");
            try {
              await uploadResume({ variables: { filename: file.name, text } });
              await refetch();
            } catch (err: unknown) {
              setError(err instanceof Error ? err.message : "Could not upload resume.");
            }
          }}
        />
        <p>
          <strong>Current resume:</strong> {data?.me?.resumeFilename || "No resume uploaded"}
        </p>
        {error && <p style={{ color: "#fca5a5" }}>{error}</p>}
        {loading && <p>Uploading...</p>}
      </div>
    </>
  );
}

export default function ResumePage() {
  return (
    <ApolloProvider client={getClient()}>
      <ResumeInner />
    </ApolloProvider>
  );
}

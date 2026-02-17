"use client";

import { ApolloProvider, useMutation, useQuery } from "@apollo/client";

import { Nav } from "@/components/Nav";
import { ME, UPLOAD_RESUME } from "@/graphql/operations";
import { getClient } from "@/lib/apollo";

function ResumeInner() {
  const { data, refetch } = useQuery(ME);
  const [uploadResume, { loading }] = useMutation(UPLOAD_RESUME);

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
            await uploadResume({ variables: { filename: file.name, text } });
            await refetch();
          }}
        />
        <p>
          <strong>Current resume:</strong> {data?.me?.resumeFilename || "No resume uploaded"}
        </p>
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

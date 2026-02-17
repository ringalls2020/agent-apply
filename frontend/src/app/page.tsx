import Link from "next/link";

export default function HomePage() {
  return (
    <div className="card">
      <h1>AgentApply</h1>
      <p>Automate opportunity discovery, applications, contact finding, and notifications with controlled preferences.</p>
      <div style={{ display: "flex", gap: 12 }}>
        <Link href="/signup"><button>Create account</button></Link>
        <Link href="/login"><button>Login</button></Link>
      </div>
    </div>
  );
}

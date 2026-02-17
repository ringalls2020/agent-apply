import "./globals.css";
import type { Metadata } from "next";

import { ApolloAppProvider } from "@/components/providers/ApolloAppProvider";

export const metadata: Metadata = {
  title: "AgentApply",
  description: "Agentic job application dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ApolloAppProvider>{children}</ApolloAppProvider>
      </body>
    </html>
  );
}

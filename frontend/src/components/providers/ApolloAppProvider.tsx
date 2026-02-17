"use client";

import { ApolloProvider } from "@apollo/client";
import type { ReactNode } from "react";

import { getClient } from "@/lib/apollo";

export function ApolloAppProvider({ children }: { children: ReactNode }) {
  return <ApolloProvider client={getClient()}>{children}</ApolloProvider>;
}

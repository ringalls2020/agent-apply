"use client";

import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";

export function getClient() {
  return new ApolloClient({
    link: new HttpLink({
      uri: "/api/graphql",
      fetch: (uri, options) => {
        const token = typeof window !== "undefined" ? localStorage.getItem("agent_apply_token")?.trim() : null;
        const headers = new Headers(options?.headers);
        if (token) headers.set("authorization", `Bearer ${token}`);
        return fetch(uri, { ...options, headers, credentials: "same-origin" });
      },
    }),
    cache: new InMemoryCache(),
  });
}

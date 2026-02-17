"use client";

import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";

export function getClient() {
  return new ApolloClient({
    link: new HttpLink({
      uri: "/api/graphql",
      fetch: (uri, options) => {
        const token = typeof window !== "undefined" ? localStorage.getItem("agent_apply_token") : null;
        const headers = {
          ...(options?.headers || {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        };
        return fetch(uri, { ...options, headers });
      },
    }),
    cache: new InMemoryCache(),
  });
}

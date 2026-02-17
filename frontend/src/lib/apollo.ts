"use client";

import { ApolloClient, HttpLink, InMemoryCache, type NormalizedCacheObject } from "@apollo/client";

import { getAuthToken } from "@/lib/authToken";

let apolloClient: ApolloClient<NormalizedCacheObject> | null = null;

export function getClient() {
  if (apolloClient) return apolloClient;

  apolloClient = new ApolloClient({
    link: new HttpLink({
      uri: "/api/graphql",
      fetch: (uri, options) => {
        const token = getAuthToken();
        const headers = new Headers(options?.headers);
        if (token) headers.set("authorization", `Bearer ${token}`);
        return fetch(uri, { ...options, headers, credentials: "same-origin" });
      },
    }),
    cache: new InMemoryCache(),
  });

  return apolloClient;
}

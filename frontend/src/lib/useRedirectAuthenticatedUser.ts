"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ME } from "@/graphql/operations";
import { clearAuthToken, getAuthToken } from "@/lib/authToken";
import { getClient } from "@/lib/apollo";

type RedirectAuthenticatedUserResult = {
  isCheckingSession: boolean;
};

const AUTH_ERROR_PATTERNS = [
  "Missing Authorization header",
  "Invalid Authorization header",
  "Invalid user auth token",
  "User auth token missing subject",
  "Unauthorized",
  "User not found for token subject",
];

function extractErrorMessages(error: unknown): string[] {
  if (!error || typeof error !== "object") return [];

  const messages: string[] = [];
  const graphQLErrors = (error as { graphQLErrors?: Array<{ message?: unknown }> }).graphQLErrors;
  if (Array.isArray(graphQLErrors)) {
    for (const item of graphQLErrors) {
      if (typeof item?.message === "string" && item.message.trim()) {
        messages.push(item.message);
      }
    }
  }

  const message = (error as { message?: unknown }).message;
  if (typeof message === "string" && message.trim()) {
    messages.push(message);
  }

  return messages;
}

function isAuthError(error: unknown): boolean {
  const messages = extractErrorMessages(error).map((message) => message.toLowerCase());
  const patterns = AUTH_ERROR_PATTERNS.map((pattern) => pattern.toLowerCase());
  return messages.some((message) => patterns.some((pattern) => message.includes(pattern)));
}

export function useRedirectAuthenticatedUser(): RedirectAuthenticatedUserResult {
  const router = useRouter();
  const [isCheckingSession, setIsCheckingSession] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const token = getAuthToken();
    if (!token) {
      setIsCheckingSession(false);
      return;
    }

    const checkSession = async () => {
      try {
        const client = getClient();
        const result = await client.query({
          query: ME,
          fetchPolicy: "network-only",
        });
        if (cancelled) return;
        if (result.data?.me) {
          router.replace("/applications");
          return;
        }
      } catch (error: unknown) {
        if (isAuthError(error)) {
          clearAuthToken();
        }
      }

      if (!cancelled) {
        setIsCheckingSession(false);
      }
    };

    void checkSession();

    return () => {
      cancelled = true;
    };
  }, [router]);

  return { isCheckingSession };
}


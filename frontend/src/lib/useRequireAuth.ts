"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getAuthToken } from "@/lib/authToken";

type RequireAuthResult = {
  isCheckingAuth: boolean;
  isAuthenticated: boolean;
};

export function useRequireAuth(): RequireAuthResult {
  const router = useRouter();
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      setIsAuthenticated(false);
      setIsCheckingAuth(false);
      router.replace("/login");
      return;
    }

    setIsAuthenticated(true);
    setIsCheckingAuth(false);
  }, [router]);

  return { isCheckingAuth, isAuthenticated };
}

const TOKEN_KEY = "agent_apply_token";

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  const token = localStorage.getItem(TOKEN_KEY)?.trim();
  return token || null;
}

export function setAuthToken(token: string): void {
  if (typeof window === "undefined") return;
  const normalized = token.trim();
  localStorage.setItem(TOKEN_KEY, normalized);
  document.cookie = `${TOKEN_KEY}=${encodeURIComponent(normalized)}; Path=/; SameSite=Lax`;
}

export function clearAuthToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
  document.cookie = `${TOKEN_KEY}=; Path=/; Max-Age=0; SameSite=Lax`;
}

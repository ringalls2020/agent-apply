import { NextRequest, NextResponse } from "next/server";

const rawBackendBaseUrl = process.env.BACKEND_API_BASE_URL ?? "http://127.0.0.1:8000";
const backendBaseUrl = rawBackendBaseUrl.endsWith("/")
  ? rawBackendBaseUrl.slice(0, -1)
  : rawBackendBaseUrl;

export async function POST(request: NextRequest) {
  const body = await request.text();
  const authorization = request.headers.get("authorization");

  const response = await fetch(`${backendBaseUrl}/graphql`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(authorization ? { authorization } : {}),
    },
    body,
    cache: "no-store",
  });

  const payload = await response.text();
  return new NextResponse(payload, {
    status: response.status,
    headers: {
      "content-type": "application/json",
    },
  });
}

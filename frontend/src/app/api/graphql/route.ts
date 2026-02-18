import { createSchema, createYoga } from "graphql-yoga";

import { resolvers, type GraphQLContext } from "./resolvers";
import { typeDefs } from "./schema";

const schema = createSchema({
  typeDefs,
  resolvers,
});

function getTokenFromRequest(request: Request): string | null {
  const authHeader = request.headers.get("authorization");
  if (authHeader) {
    const bearerMatch = authHeader.match(/^Bearer\s+(.+)$/i);
    if (bearerMatch?.[1]) return bearerMatch[1].trim();
  }

  const cookieHeader = request.headers.get("cookie");
  if (!cookieHeader) return null;

  const tokenCookie = cookieHeader
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("agent_apply_token="));

  if (!tokenCookie) return null;
  const [, token = ""] = tokenCookie.split("=");
  return token ? decodeURIComponent(token) : null;
}

const yoga = createYoga<GraphQLContext>({
  graphqlEndpoint: "/api/graphql",
  schema,
  fetchAPI: { Response },
});

async function handleGraphQL(request: Request): Promise<Response> {
  return yoga.handleRequest(request, { token: getTokenFromRequest(request) });
}

export { handleGraphQL as GET, handleGraphQL as POST };

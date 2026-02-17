import { createSchema, createYoga } from "graphql-yoga";

import {
  generateApplications,
  getApplications,
  login,
  requireUser,
  signup,
  updatePreferences,
  updateResume,
} from "@/lib/store";

type GraphQLContext = {
  token: string | null;
};

const typeDefs = /* GraphQL */ `
  type User {
    id: ID!
    name: String!
    email: String!
    interests: [String!]!
    applicationsPerDay: Int!
    resumeFilename: String
    resumeText: String
  }

  type Application {
    id: ID!
    title: String!
    company: String!
    status: String!
    contactName: String!
    contactEmail: String!
    submittedAt: String!
  }

  type AuthPayload {
    token: String!
    user: User!
  }

  type Query {
    me: User!
    applications: [Application!]!
  }

  type Mutation {
    signup(name: String!, email: String!, password: String!): AuthPayload!
    login(email: String!, password: String!): AuthPayload!
    updatePreferences(interests: [String!]!, applicationsPerDay: Int!): User!
    uploadResume(filename: String!, text: String!): User!
    runAgent: [Application!]!
  }
`;

const schema = createSchema({
  typeDefs,
  resolvers: {
    Query: {
      me: (_root, _args, ctx: GraphQLContext) => requireUser(ctx.token),
      applications: (_root, _args, ctx: GraphQLContext) => getApplications(ctx.token),
    },
    Mutation: {
      signup: (_root, args) => signup(args.name, args.email, args.password),
      login: (_root, args) => login(args.email, args.password),
      updatePreferences: (_root, args, ctx: GraphQLContext) =>
        updatePreferences(ctx.token, args.interests, args.applicationsPerDay),
      uploadResume: (_root, args, ctx: GraphQLContext) => updateResume(ctx.token, args.filename, args.text),
      runAgent: (_root, _args, ctx: GraphQLContext) => generateApplications(ctx.token),
    },
  },
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

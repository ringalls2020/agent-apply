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
      me: (_root, _args, ctx) => requireUser(ctx.token),
      applications: (_root, _args, ctx) => getApplications(ctx.token),
    },
    Mutation: {
      signup: (_root, args) => signup(args.name, args.email, args.password),
      login: (_root, args) => login(args.email, args.password),
      updatePreferences: (_root, args, ctx) => updatePreferences(ctx.token, args.interests, args.applicationsPerDay),
      uploadResume: (_root, args, ctx) => updateResume(ctx.token, args.filename, args.text),
      runAgent: (_root, _args, ctx) => generateApplications(ctx.token),
    },
  },
});

const yoga = createYoga<{ token: string | null }>({
  graphqlEndpoint: "/api/graphql",
  schema,
  context: async ({ request }) => {
    const auth = request.headers.get("authorization");
    const token = auth?.startsWith("Bearer ") ? auth.replace("Bearer ", "") : null;
    return { token };
  },
  fetchAPI: { Response },
});

export { yoga as GET, yoga as POST };

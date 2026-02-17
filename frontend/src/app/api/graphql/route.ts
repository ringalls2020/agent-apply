import { createSchema, createYoga } from "graphql-yoga";

import { BackendRequestError, requestBackend } from "@/lib/backendClient";

type GraphQLContext = {
  token: string | null;
};

type BackendAuthUser = {
  id: string;
  full_name: string;
  email: string;
  interests: string[];
  applications_per_day: number;
  resume_filename: string | null;
};

type BackendAuthResponse = {
  token: string;
  user: BackendAuthUser;
};

type BackendApplication = {
  id: string;
  status: string;
  opportunity: {
    title: string;
    company: string;
  };
  contact: {
    name: string;
    email: string;
  } | null;
  submitted_at: string | null;
};

type BackendApplicationsResponse = {
  applications: BackendApplication[];
};

type BackendPreferenceResponse = {
  interests: string[];
  locations: string[];
  seniority: string | null;
  applications_per_day: number;
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

function requireToken(token: string | null): string {
  if (!token) {
    throw new Error("Unauthorized");
  }
  return token;
}

function toGraphQLUser(user: BackendAuthUser) {
  return {
    id: user.id,
    name: user.full_name,
    email: user.email,
    interests: user.interests,
    applicationsPerDay: user.applications_per_day,
    resumeFilename: user.resume_filename,
    resumeText: null,
  };
}

function toGraphQLApplication(application: BackendApplication) {
  return {
    id: application.id,
    title: application.opportunity.title,
    company: application.opportunity.company,
    status: application.status,
    contactName: application.contact?.name ?? "",
    contactEmail: application.contact?.email ?? "",
    submittedAt: application.submitted_at ?? "",
  };
}

async function getAuthenticatedUser(token: string) {
  return requestBackend<BackendAuthUser>("/v1/auth/me", { token });
}

const schema = createSchema({
  typeDefs,
  resolvers: {
    Query: {
      me: async (_root, _args, ctx: GraphQLContext) => {
        const user = await getAuthenticatedUser(requireToken(ctx.token));
        return toGraphQLUser(user);
      },
      applications: async (_root, _args, ctx: GraphQLContext) => {
        const token = requireToken(ctx.token);
        const response = await requestBackend<BackendApplicationsResponse>(
          "/v1/applications",
          { token },
        );
        return response.applications.map(toGraphQLApplication);
      },
    },
    Mutation: {
      signup: async (_root, args) => {
        const response = await requestBackend<BackendAuthResponse>(
          "/v1/auth/signup",
          {
            method: "POST",
            body: {
              full_name: args.name,
              email: args.email,
              password: args.password,
            },
          },
        );
        return {
          token: response.token,
          user: toGraphQLUser(response.user),
        };
      },
      login: async (_root, args) => {
        const response = await requestBackend<BackendAuthResponse>(
          "/v1/auth/login",
          {
            method: "POST",
            body: {
              email: args.email,
              password: args.password,
            },
          },
        );
        return {
          token: response.token,
          user: toGraphQLUser(response.user),
        };
      },
      updatePreferences: async (_root, args, ctx: GraphQLContext) => {
        const token = requireToken(ctx.token);
        const user = await getAuthenticatedUser(token);
        let currentPreferences: BackendPreferenceResponse = {
          interests: [],
          locations: [],
          seniority: null,
          applications_per_day: args.applicationsPerDay,
        };
        try {
          currentPreferences = await requestBackend<BackendPreferenceResponse>(
            `/v1/users/${user.id}/preferences`,
            { token },
          );
        } catch (error: unknown) {
          if (!(error instanceof BackendRequestError && error.status === 404)) {
            throw error;
          }
        }

        await requestBackend(`/v1/users/${user.id}/preferences`, {
          method: "PUT",
          token,
          body: {
            interests: args.interests,
            applications_per_day: args.applicationsPerDay,
            locations: currentPreferences.locations,
            seniority: currentPreferences.seniority,
          },
        });

        const refreshedUser = await getAuthenticatedUser(token);
        return toGraphQLUser(refreshedUser);
      },
      uploadResume: async (_root, args, ctx: GraphQLContext) => {
        const token = requireToken(ctx.token);
        const user = await getAuthenticatedUser(token);

        await requestBackend(`/v1/users/${user.id}/resume`, {
          method: "PUT",
          token,
          body: {
            filename: args.filename,
            resume_text: args.text,
          },
        });

        const refreshedUser = await getAuthenticatedUser(token);
        return toGraphQLUser(refreshedUser);
      },
      runAgent: async (_root, _args, ctx: GraphQLContext) => {
        const token = requireToken(ctx.token);
        const response = await requestBackend<BackendApplicationsResponse>(
          "/v1/agent/run",
          {
            method: "POST",
            token,
          },
        );
        return response.applications.map(toGraphQLApplication);
      },
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

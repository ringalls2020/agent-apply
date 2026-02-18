import { BackendRequestError, requestBackend } from "@/lib/backendClient";

import type {
  BackendApplication,
  BackendApplicationProfile,
  BackendApplicationsResponse,
  BackendApplicationsSearchResponse,
  BackendAuthResponse,
  BackendAuthUser,
  BackendBulkApplyResponse,
  BackendPreferenceResponse,
  GraphQLApplicationsFilterInput,
  GraphQLProfileInput,
} from "./backend";
import {
  buildApplicationsSearchPath,
  getProfileOrDefault,
  requireToken,
  toBackendProfilePayload,
} from "./backend";
import {
  toGraphQLApplication,
  toGraphQLApplicationProfile,
  toGraphQLBulkApplyResult,
  toGraphQLUser,
} from "./mappers";

export type GraphQLContext = {
  token: string | null;
  userPromise?: Promise<BackendAuthUser>;
  user?: BackendAuthUser;
};

async function getMemoizedAuthenticatedUser(ctx: GraphQLContext): Promise<BackendAuthUser> {
  if (ctx.user) return ctx.user;
  if (!ctx.userPromise) {
    const token = requireToken(ctx.token);
    ctx.userPromise = requestBackend<BackendAuthUser>("/v1/auth/me", { token });
  }
  ctx.user = await ctx.userPromise;
  return ctx.user;
}

export const resolvers = {
  Query: {
    me: async (_root: unknown, _args: unknown, ctx: GraphQLContext) => {
      const user = await getMemoizedAuthenticatedUser(ctx);
      return toGraphQLUser(user);
    },
    applications: async (
      _root: unknown,
      args: { includeArchived?: boolean },
      ctx: GraphQLContext,
    ) => {
      const token = requireToken(ctx.token);
      const includeArchived = Boolean(args.includeArchived);
      const response = await requestBackend<BackendApplicationsResponse>(
        `/v1/applications?include_archived=${includeArchived ? "true" : "false"}`,
        { token },
      );
      return response.applications.map(toGraphQLApplication);
    },
    applicationsSearch: async (
      _root: unknown,
      args: { filter?: GraphQLApplicationsFilterInput; limit?: number; offset?: number },
      ctx: GraphQLContext,
    ) => {
      const token = requireToken(ctx.token);
      const limit = Math.min(Math.max(args.limit ?? 25, 1), 100);
      const offset = Math.max(args.offset ?? 0, 0);
      const path = buildApplicationsSearchPath(args.filter, limit, offset);
      const response = await requestBackend<BackendApplicationsSearchResponse>(path, { token });
      return {
        applications: response.applications.map(toGraphQLApplication),
        totalCount: response.total_count,
        limit: response.limit,
        offset: response.offset,
      };
    },
    profile: async (_root: unknown, _args: unknown, ctx: GraphQLContext) => {
      const token = requireToken(ctx.token);
      const user = await getMemoizedAuthenticatedUser(ctx);
      const profile = await getProfileOrDefault(token, user.id);
      return toGraphQLApplicationProfile(profile);
    },
  },
  Mutation: {
    signup: async (_root: unknown, args: { name: string; email: string; password: string }) => {
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
    login: async (_root: unknown, args: { email: string; password: string }) => {
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
    updatePreferences: async (
      _root: unknown,
      args: { interests: string[]; applicationsPerDay: number },
      ctx: GraphQLContext,
    ) => {
      const token = requireToken(ctx.token);
      const user = await getMemoizedAuthenticatedUser(ctx);
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

      const refreshedUser = await requestBackend<BackendAuthUser>("/v1/auth/me", { token });
      ctx.user = refreshedUser;
      ctx.userPromise = Promise.resolve(refreshedUser);
      return toGraphQLUser(refreshedUser);
    },
    uploadResume: async (
      _root: unknown,
      args: { filename: string; text: string },
      ctx: GraphQLContext,
    ) => {
      const token = requireToken(ctx.token);
      const user = await getMemoizedAuthenticatedUser(ctx);

      await requestBackend(`/v1/users/${user.id}/resume`, {
        method: "PUT",
        token,
        body: {
          filename: args.filename,
          resume_text: args.text,
        },
      });

      const refreshedUser = await requestBackend<BackendAuthUser>("/v1/auth/me", { token });
      ctx.user = refreshedUser;
      ctx.userPromise = Promise.resolve(refreshedUser);
      return toGraphQLUser(refreshedUser);
    },
    runAgent: async (_root: unknown, _args: unknown, ctx: GraphQLContext) => {
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
    applySelectedApplications: async (
      _root: unknown,
      args: { applicationIds: string[] },
      ctx: GraphQLContext,
    ) => {
      const token = requireToken(ctx.token);
      const response = await requestBackend<BackendBulkApplyResponse>(
        "/v1/applications/apply",
        {
          method: "POST",
          token,
          body: { application_ids: args.applicationIds },
        },
      );
      return toGraphQLBulkApplyResult(response);
    },
    markApplicationViewed: async (
      _root: unknown,
      args: { applicationId: string },
      ctx: GraphQLContext,
    ) => {
      const token = requireToken(ctx.token);
      const response = await requestBackend<BackendApplication>(
        `/v1/applications/${args.applicationId}/mark-viewed`,
        {
          method: "POST",
          token,
        },
      );
      return toGraphQLApplication(response);
    },
    markApplicationApplied: async (
      _root: unknown,
      args: { applicationId: string },
      ctx: GraphQLContext,
    ) => {
      const token = requireToken(ctx.token);
      const response = await requestBackend<BackendApplication>(
        `/v1/applications/${args.applicationId}/mark-applied`,
        {
          method: "POST",
          token,
        },
      );
      return toGraphQLApplication(response);
    },
    updateProfile: async (
      _root: unknown,
      args: { input: GraphQLProfileInput },
      ctx: GraphQLContext,
    ) => {
      const token = requireToken(ctx.token);
      const user = await getMemoizedAuthenticatedUser(ctx);
      const current = await getProfileOrDefault(token, user.id);
      const payload = toBackendProfilePayload(args.input, current);

      const updated = await requestBackend<BackendApplicationProfile>(
        `/v1/users/${user.id}/profile`,
        {
          method: "PUT",
          token,
          body: payload,
        },
      );

      return toGraphQLApplicationProfile(updated);
    },
  },
};

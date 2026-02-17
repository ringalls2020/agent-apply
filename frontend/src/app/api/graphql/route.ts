import { createSchema, createYoga } from "graphql-yoga";

import { BackendRequestError, requestBackend } from "@/lib/backendClient";
import { typeDefs } from "./schema";

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
  autosubmit_enabled: boolean;
};

type BackendAuthResponse = {
  token: string;
  user: BackendAuthUser;
};

type BackendApplication = {
  id: string;
  status: string;
  is_archived: boolean;
  opportunity: {
    title: string;
    company: string;
    url: string;
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

type BackendApplicationsSearchResponse = {
  applications: BackendApplication[];
  total_count: number;
  limit: number;
  offset: number;
};

type BackendBulkApplySkippedItem = {
  application_id: string;
  reason: string;
  status: string | null;
};

type BackendBulkApplyResponse = {
  run_id: string | null;
  status_url: string | null;
  accepted_application_ids: string[];
  skipped: BackendBulkApplySkippedItem[];
  applications: BackendApplication[];
};

type BackendPreferenceResponse = {
  interests: string[];
  locations: string[];
  seniority: string | null;
  applications_per_day: number;
};

type BackendCustomAnswer = {
  question_key: string;
  answer: string;
};

type BackendSensitiveProfile = {
  gender: string;
  race_ethnicity: string;
  veteran_status: string;
  disability_status: string;
};

type BackendApplicationProfile = {
  user_id: string;
  autosubmit_enabled: boolean;
  phone: string | null;
  city: string | null;
  state: string | null;
  country: string | null;
  linkedin_url: string | null;
  github_url: string | null;
  portfolio_url: string | null;
  work_authorization: string | null;
  requires_sponsorship: boolean | null;
  willing_to_relocate: boolean | null;
  years_experience: number | null;
  writing_voice: string | null;
  cover_letter_style: string | null;
  achievements_summary: string | null;
  custom_answers: BackendCustomAnswer[];
  additional_context: string | null;
  sensitive: BackendSensitiveProfile;
};

type GraphQLCustomAnswerInput = {
  questionKey: string;
  answer: string;
};

type GraphQLSensitiveInput = {
  gender?: string | null;
  raceEthnicity?: string | null;
  veteranStatus?: string | null;
  disabilityStatus?: string | null;
};

type GraphQLProfileInput = {
  autosubmitEnabled: boolean;
  phone?: string | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  linkedinUrl?: string | null;
  githubUrl?: string | null;
  portfolioUrl?: string | null;
  workAuthorization?: string | null;
  requiresSponsorship?: boolean | null;
  willingToRelocate?: boolean | null;
  yearsExperience?: number | null;
  writingVoice?: string | null;
  coverLetterStyle?: string | null;
  achievementsSummary?: string | null;
  customAnswers?: GraphQLCustomAnswerInput[];
  additionalContext?: string | null;
  sensitive?: GraphQLSensitiveInput | null;
};

type GraphQLApplicationsFilterInput = {
  statuses?: string[] | null;
  q?: string | null;
  companies?: string[] | null;
  sources?: string[] | null;
  includeArchived?: boolean | null;
  hasContact?: boolean | null;
  discoveredFrom?: string | null;
  discoveredTo?: string | null;
  sortBy?: string | null;
  sortDir?: string | null;
};


function requireToken(token: string | null): string {
  if (!token) {
    throw new Error("Unauthorized");
  }
  return token;
}

function deriveApplicationSource(jobUrl: string): string {
  try {
    const host = new URL(jobUrl).hostname.toLowerCase();
    if (host.includes("greenhouse")) return "greenhouse";
    if (host.includes("lever.co")) return "lever";
    if (host.includes("smartrecruiters")) return "smartrecruiters";
    if (host.includes("myworkdayjobs.com") || host.includes("workday")) return "workday";
    return "other";
  } catch {
    return "other";
  }
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
    autosubmitEnabled: user.autosubmit_enabled,
  };
}

function toGraphQLApplication(application: BackendApplication) {
  return {
    id: application.id,
    title: application.opportunity.title,
    company: application.opportunity.company,
    status: application.status,
    isArchived: application.is_archived,
    source: deriveApplicationSource(application.opportunity.url),
    contactName: application.contact?.name ?? "",
    contactEmail: application.contact?.email ?? "",
    submittedAt: application.submitted_at ?? "",
    jobUrl: application.opportunity.url,
  };
}

function toGraphQLBulkApplyResult(response: BackendBulkApplyResponse) {
  return {
    runId: response.run_id,
    statusUrl: response.status_url,
    acceptedApplicationIds: response.accepted_application_ids,
    skipped: response.skipped.map((item) => ({
      applicationId: item.application_id,
      reason: item.reason,
      status: item.status,
    })),
    applications: response.applications.map(toGraphQLApplication),
  };
}

function toGraphQLApplicationProfile(profile: BackendApplicationProfile) {
  return {
    autosubmitEnabled: profile.autosubmit_enabled,
    phone: profile.phone,
    city: profile.city,
    state: profile.state,
    country: profile.country,
    linkedinUrl: profile.linkedin_url,
    githubUrl: profile.github_url,
    portfolioUrl: profile.portfolio_url,
    workAuthorization: profile.work_authorization,
    requiresSponsorship: profile.requires_sponsorship,
    willingToRelocate: profile.willing_to_relocate,
    yearsExperience: profile.years_experience,
    writingVoice: profile.writing_voice,
    coverLetterStyle: profile.cover_letter_style,
    achievementsSummary: profile.achievements_summary,
    customAnswers: profile.custom_answers.map((item) => ({
      questionKey: item.question_key,
      answer: item.answer,
    })),
    additionalContext: profile.additional_context,
    sensitive: {
      gender: profile.sensitive.gender,
      raceEthnicity: profile.sensitive.race_ethnicity,
      veteranStatus: profile.sensitive.veteran_status,
      disabilityStatus: profile.sensitive.disability_status,
    },
  };
}

async function getAuthenticatedUser(token: string) {
  return requestBackend<BackendAuthUser>("/v1/auth/me", { token });
}

function defaultProfile(userId: string): BackendApplicationProfile {
  return {
    user_id: userId,
    autosubmit_enabled: false,
    phone: null,
    city: null,
    state: null,
    country: null,
    linkedin_url: null,
    github_url: null,
    portfolio_url: null,
    work_authorization: null,
    requires_sponsorship: null,
    willing_to_relocate: null,
    years_experience: null,
    writing_voice: null,
    cover_letter_style: null,
    achievements_summary: null,
    custom_answers: [],
    additional_context: null,
    sensitive: {
      gender: "decline_to_answer",
      race_ethnicity: "decline_to_answer",
      veteran_status: "decline_to_answer",
      disability_status: "decline_to_answer",
    },
  };
}

async function getProfileOrDefault(token: string, userId: string): Promise<BackendApplicationProfile> {
  try {
    return await requestBackend<BackendApplicationProfile>(`/v1/users/${userId}/profile`, {
      token,
    });
  } catch (error: unknown) {
    if (error instanceof BackendRequestError && error.status === 404) {
      return defaultProfile(userId);
    }
    throw error;
  }
}

function toBackendProfilePayload(input: GraphQLProfileInput, current: BackendApplicationProfile) {
  const mergedSensitive = {
    gender: input.sensitive?.gender ?? current.sensitive.gender,
    race_ethnicity: input.sensitive?.raceEthnicity ?? current.sensitive.race_ethnicity,
    veteran_status: input.sensitive?.veteranStatus ?? current.sensitive.veteran_status,
    disability_status: input.sensitive?.disabilityStatus ?? current.sensitive.disability_status,
  };

  return {
    autosubmit_enabled: input.autosubmitEnabled,
    phone: input.phone ?? current.phone,
    city: input.city ?? current.city,
    state: input.state ?? current.state,
    country: input.country ?? current.country,
    linkedin_url: input.linkedinUrl ?? current.linkedin_url,
    github_url: input.githubUrl ?? current.github_url,
    portfolio_url: input.portfolioUrl ?? current.portfolio_url,
    work_authorization: input.workAuthorization ?? current.work_authorization,
    requires_sponsorship: input.requiresSponsorship ?? current.requires_sponsorship,
    willing_to_relocate: input.willingToRelocate ?? current.willing_to_relocate,
    years_experience: input.yearsExperience ?? current.years_experience,
    writing_voice: input.writingVoice ?? current.writing_voice,
    cover_letter_style: input.coverLetterStyle ?? current.cover_letter_style,
    achievements_summary: input.achievementsSummary ?? current.achievements_summary,
    custom_answers:
      input.customAnswers?.map((item) => ({
        question_key: item.questionKey,
        answer: item.answer,
      })) ?? current.custom_answers,
    additional_context: input.additionalContext ?? current.additional_context,
    sensitive: mergedSensitive,
  };
}

function buildApplicationsSearchPath(
  filter: GraphQLApplicationsFilterInput | null | undefined,
  limit: number,
  offset: number,
): string {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  params.set("offset", String(offset));

  if (filter?.q?.trim()) params.set("q", filter.q.trim());
  for (const status of filter?.statuses ?? []) {
    if (status?.trim()) params.append("statuses", status.trim());
  }
  for (const company of filter?.companies ?? []) {
    if (company?.trim()) params.append("companies", company.trim());
  }
  for (const source of filter?.sources ?? []) {
    if (source?.trim()) params.append("sources", source.trim());
  }
  if (filter?.includeArchived) {
    params.set("include_archived", "true");
  }
  if (typeof filter?.hasContact === "boolean") {
    params.set("has_contact", String(filter.hasContact));
  }
  if (filter?.discoveredFrom?.trim()) {
    params.set("discovered_from", filter.discoveredFrom.trim());
  }
  if (filter?.discoveredTo?.trim()) {
    params.set("discovered_to", filter.discoveredTo.trim());
  }
  if (filter?.sortBy?.trim()) params.set("sort_by", filter.sortBy.trim());
  if (filter?.sortDir?.trim()) params.set("sort_dir", filter.sortDir.trim());

  return `/v1/applications/search?${params.toString()}`;
}

const schema = createSchema({
  typeDefs,
  resolvers: {
    Query: {
      me: async (_root, _args, ctx: GraphQLContext) => {
        const user = await getAuthenticatedUser(requireToken(ctx.token));
        return toGraphQLUser(user);
      },
      applications: async (
        _root,
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
        _root,
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
      profile: async (_root, _args, ctx: GraphQLContext) => {
        const token = requireToken(ctx.token);
        const user = await getAuthenticatedUser(token);
        const profile = await getProfileOrDefault(token, user.id);
        return toGraphQLApplicationProfile(profile);
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
      applySelectedApplications: async (
        _root,
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
        _root,
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
        _root,
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
        _root,
        args: { input: GraphQLProfileInput },
        ctx: GraphQLContext,
      ) => {
        const token = requireToken(ctx.token);
        const user = await getAuthenticatedUser(token);
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

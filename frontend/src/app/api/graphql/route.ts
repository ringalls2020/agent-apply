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
  autosubmit_enabled: boolean;
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

const typeDefs = /* GraphQL */ `
  type User {
    id: ID!
    name: String!
    email: String!
    interests: [String!]!
    applicationsPerDay: Int!
    resumeFilename: String
    resumeText: String
    autosubmitEnabled: Boolean!
  }

  type Application {
    id: ID!
    title: String!
    company: String!
    status: String!
    contactName: String!
    contactEmail: String!
    submittedAt: String!
    jobUrl: String!
  }

  type AuthPayload {
    token: String!
    user: User!
  }

  type CustomAnswerOverride {
    questionKey: String!
    answer: String!
  }

  type SensitiveProfile {
    gender: String!
    raceEthnicity: String!
    veteranStatus: String!
    disabilityStatus: String!
  }

  type ApplicationProfile {
    autosubmitEnabled: Boolean!
    phone: String
    city: String
    state: String
    country: String
    linkedinUrl: String
    githubUrl: String
    portfolioUrl: String
    workAuthorization: String
    requiresSponsorship: Boolean
    willingToRelocate: Boolean
    yearsExperience: Int
    writingVoice: String
    coverLetterStyle: String
    achievementsSummary: String
    customAnswers: [CustomAnswerOverride!]!
    additionalContext: String
    sensitive: SensitiveProfile!
  }

  input CustomAnswerOverrideInput {
    questionKey: String!
    answer: String!
  }

  input SensitiveProfileInput {
    gender: String
    raceEthnicity: String
    veteranStatus: String
    disabilityStatus: String
  }

  input ApplicationProfileInput {
    autosubmitEnabled: Boolean!
    phone: String
    city: String
    state: String
    country: String
    linkedinUrl: String
    githubUrl: String
    portfolioUrl: String
    workAuthorization: String
    requiresSponsorship: Boolean
    willingToRelocate: Boolean
    yearsExperience: Int
    writingVoice: String
    coverLetterStyle: String
    achievementsSummary: String
    customAnswers: [CustomAnswerOverrideInput!]
    additionalContext: String
    sensitive: SensitiveProfileInput
  }

  type Query {
    me: User!
    applications: [Application!]!
    profile: ApplicationProfile!
  }

  type Mutation {
    signup(name: String!, email: String!, password: String!): AuthPayload!
    login(email: String!, password: String!): AuthPayload!
    updatePreferences(interests: [String!]!, applicationsPerDay: Int!): User!
    uploadResume(filename: String!, text: String!): User!
    runAgent: [Application!]!
    updateProfile(input: ApplicationProfileInput!): ApplicationProfile!
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
    autosubmitEnabled: user.autosubmit_enabled,
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
    jobUrl: application.opportunity.url,
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

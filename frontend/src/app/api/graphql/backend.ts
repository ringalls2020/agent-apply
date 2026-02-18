import { BackendRequestError, requestBackend } from "@/lib/backendClient";

export type BackendAuthUser = {
  id: string;
  full_name: string;
  email: string;
  interests: string[];
  applications_per_day: number;
  resume_filename: string | null;
  autosubmit_enabled: boolean;
};

export type BackendAuthResponse = {
  token: string;
  user: BackendAuthUser;
};

export type BackendApplication = {
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

export type BackendApplicationsResponse = {
  applications: BackendApplication[];
};

export type BackendApplicationsSearchResponse = {
  applications: BackendApplication[];
  total_count: number;
  limit: number;
  offset: number;
};

export type BackendBulkApplySkippedItem = {
  application_id: string;
  reason: string;
  status: string | null;
};

export type BackendBulkApplyResponse = {
  run_id: string | null;
  status_url: string | null;
  accepted_application_ids: string[];
  skipped: BackendBulkApplySkippedItem[];
  applications: BackendApplication[];
};

export type BackendPreferenceResponse = {
  interests: string[];
  locations: string[];
  seniority: string | null;
  applications_per_day: number;
};

export type BackendCustomAnswer = {
  question_key: string;
  answer: string;
};

export type BackendSensitiveProfile = {
  gender: string;
  race_ethnicity: string;
  veteran_status: string;
  disability_status: string;
};

export type BackendApplicationProfile = {
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

export type GraphQLCustomAnswerInput = {
  questionKey: string;
  answer: string;
};

export type GraphQLSensitiveInput = {
  gender?: string | null;
  raceEthnicity?: string | null;
  veteranStatus?: string | null;
  disabilityStatus?: string | null;
};

export type GraphQLProfileInput = {
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

export type GraphQLApplicationsFilterInput = {
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

export function requireToken(token: string | null): string {
  if (!token) {
    throw new Error("Unauthorized");
  }
  return token;
}

export async function getAuthenticatedUser(token: string) {
  return requestBackend<BackendAuthUser>("/v1/auth/me", { token });
}

export function defaultProfile(userId: string): BackendApplicationProfile {
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

export async function getProfileOrDefault(
  token: string,
  userId: string,
): Promise<BackendApplicationProfile> {
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

export function toBackendProfilePayload(
  input: GraphQLProfileInput,
  current: BackendApplicationProfile,
) {
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

export function buildApplicationsSearchPath(
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

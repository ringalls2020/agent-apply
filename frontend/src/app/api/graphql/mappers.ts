import type {
  BackendApplication,
  BackendApplicationProfile,
  BackendAuthUser,
  BackendBulkApplyResponse,
} from "./backend";

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

export function toGraphQLUser(user: BackendAuthUser) {
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

export function toGraphQLApplication(application: BackendApplication) {
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

export function toGraphQLBulkApplyResult(response: BackendBulkApplyResponse) {
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

export function toGraphQLApplicationProfile(profile: BackendApplicationProfile) {
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

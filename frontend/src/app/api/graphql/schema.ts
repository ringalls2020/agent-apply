export const typeDefs = /* GraphQL */ `
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
    source: String!
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

  type ApplicationsSearchResult {
    applications: [Application!]!
    totalCount: Int!
    limit: Int!
    offset: Int!
  }

  type BulkApplySkippedItem {
    applicationId: ID!
    reason: String!
    status: String
  }

  type BulkApplyResult {
    runId: String
    statusUrl: String
    acceptedApplicationIds: [ID!]!
    skipped: [BulkApplySkippedItem!]!
    applications: [Application!]!
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

  input ApplicationFilterInput {
    statuses: [String!]
    q: String
    companies: [String!]
    sources: [String!]
    hasContact: Boolean
    discoveredFrom: String
    discoveredTo: String
    sortBy: String
    sortDir: String
  }

  type Query {
    me: User!
    applications: [Application!]!
    applicationsSearch(filter: ApplicationFilterInput, limit: Int = 25, offset: Int = 0): ApplicationsSearchResult!
    profile: ApplicationProfile!
  }

  type Mutation {
    signup(name: String!, email: String!, password: String!): AuthPayload!
    login(email: String!, password: String!): AuthPayload!
    updatePreferences(interests: [String!]!, applicationsPerDay: Int!): User!
    uploadResume(filename: String!, text: String!): User!
    runAgent: [Application!]!
    applySelectedApplications(applicationIds: [ID!]!): BulkApplyResult!
    markApplicationViewed(applicationId: ID!): Application!
    markApplicationApplied(applicationId: ID!): Application!
    updateProfile(input: ApplicationProfileInput!): ApplicationProfile!
  }
`;

import { gql } from "@apollo/client";

export const SIGNUP = gql`
  mutation Signup($fullName: String!, $email: String!, $password: String!) {
    signup(fullName: $fullName, email: $email, password: $password) {
      token
      user {
        id
        fullName
        email
      }
    }
  }
`;

export const LOGIN = gql`
  mutation Login($email: String!, $password: String!) {
    login(email: $email, password: $password) {
      token
      user {
        id
        fullName
        email
      }
    }
  }
`;

export const ME = gql`
  query Me {
    me {
      id
      fullName
      email
      interests
      applicationsPerDay
      resumeFilename
      autosubmitEnabled
    }
  }
`;

export const UPDATE_PREFERENCES = gql`
  mutation UpdatePreferences($interests: [String!]!, $applicationsPerDay: Int!) {
    updatePreferences(interests: $interests, applicationsPerDay: $applicationsPerDay) {
      userId
      interests
      applicationsPerDay
    }
  }
`;

export const UPLOAD_RESUME = gql`
  mutation UploadResume(
    $filename: String!
    $resumeText: String
    $fileContentBase64: String
    $fileMimeType: String
  ) {
    uploadResume(
      filename: $filename
      resumeText: $resumeText
      fileContentBase64: $fileContentBase64
      fileMimeType: $fileMimeType
    ) {
      id
      filename
    }
  }
`;

export const RUN_AGENT = gql`
  mutation RunAgent {
    runAgent {
      id
      status
      title
      company
      source
      jobUrl
    }
  }
`;

export const APPLICATIONS_SEARCH = gql`
  query ApplicationsSearch($filter: ApplicationFilterInput, $limit: Int, $offset: Int) {
    applicationsSearch(filter: $filter, limit: $limit, offset: $offset) {
      totalCount
      limit
      offset
      applications {
        id
        title
        company
        status
        isArchived
        source
        contactName
        contactEmail
        submittedAt
        jobUrl
      }
    }
  }
`;

export const APPLY_SELECTED_APPLICATIONS = gql`
  mutation ApplySelectedApplications($applicationIds: [ID!]!) {
    applySelectedApplications(applicationIds: $applicationIds) {
      runId
      statusUrl
      acceptedApplicationIds
      skipped {
        applicationId
        reason
        status
      }
      applications {
        id
        status
        source
        jobUrl
      }
    }
  }
`;

export const MARK_APPLICATION_VIEWED = gql`
  mutation MarkApplicationViewed($applicationId: ID!) {
    markApplicationViewed(applicationId: $applicationId) {
      id
      status
      source
      jobUrl
    }
  }
`;

export const MARK_APPLICATION_APPLIED = gql`
  mutation MarkApplicationApplied($applicationId: ID!) {
    markApplicationApplied(applicationId: $applicationId) {
      id
      status
      source
      submittedAt
      jobUrl
    }
  }
`;

export const PROFILE = gql`
  query Profile {
    profile {
      autosubmitEnabled
      phone
      city
      state
      country
      linkedinUrl
      githubUrl
      portfolioUrl
      workAuthorization
      requiresSponsorship
      willingToRelocate
      yearsExperience
      writingVoice
      coverLetterStyle
      achievementsSummary
      additionalContext
      customAnswers {
        questionKey
        answer
      }
      sensitive {
        gender
        raceEthnicity
        veteranStatus
        disabilityStatus
      }
    }
  }
`;

export const UPDATE_PROFILE = gql`
  mutation UpdateProfile($input: ApplicationProfileInput!) {
    updateProfile(input: $input) {
      autosubmitEnabled
      phone
      city
      state
      country
      linkedinUrl
      githubUrl
      portfolioUrl
      workAuthorization
      requiresSponsorship
      willingToRelocate
      yearsExperience
      writingVoice
      coverLetterStyle
      achievementsSummary
      additionalContext
      customAnswers {
        questionKey
        answer
      }
      sensitive {
        gender
        raceEthnicity
        veteranStatus
        disabilityStatus
      }
    }
  }
`;

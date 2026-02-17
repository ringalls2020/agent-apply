import { gql } from "@apollo/client";

export const SIGNUP = gql`
  mutation Signup($name: String!, $email: String!, $password: String!) {
    signup(name: $name, email: $email, password: $password) {
      token
      user {
        id
        name
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
        name
        email
      }
    }
  }
`;

export const ME = gql`
  query Me {
    me {
      id
      name
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
      id
      interests
      applicationsPerDay
    }
  }
`;

export const UPLOAD_RESUME = gql`
  mutation UploadResume($filename: String!, $text: String!) {
    uploadResume(filename: $filename, text: $text) {
      id
      resumeFilename
    }
  }
`;

export const RUN_AGENT = gql`
  mutation RunAgent {
    runAgent {
      id
      title
      status
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

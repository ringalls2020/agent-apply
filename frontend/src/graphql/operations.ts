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

export const APPLICATIONS = gql`
  query Applications {
    applications {
      id
      title
      company
      status
      contactName
      contactEmail
      submittedAt
    }
  }
`;

export const RUN_AGENT = gql`
  mutation RunAgent {
    runAgent {
      id
      title
    }
  }
`;

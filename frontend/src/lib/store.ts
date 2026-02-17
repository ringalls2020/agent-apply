import crypto from "node:crypto";

export type UserRecord = {
  id: string;
  name: string;
  email: string;
  passwordHash: string;
  interests: string[];
  applicationsPerDay: number;
  resumeText: string;
  resumeFilename: string;
};

export type ApplicationRecord = {
  id: string;
  userId: string;
  title: string;
  company: string;
  status: "submitted" | "contact_found" | "notified";
  contactName: string;
  contactEmail: string;
  submittedAt: string;
};

const db = {
  users: new Map<string, UserRecord>(),
  emailToUserId: new Map<string, string>(),
  sessions: new Map<string, string>(),
  applications: [] as ApplicationRecord[],
};

const hashPassword = (password: string) =>
  crypto.createHash("sha256").update(password).digest("hex");

export function signup(name: string, email: string, password: string): { token: string; user: UserRecord } {
  const normalizedEmail = email.trim().toLowerCase();
  if (db.emailToUserId.has(normalizedEmail)) {
    throw new Error("Account with this email already exists.");
  }

  const user: UserRecord = {
    id: crypto.randomUUID(),
    name,
    email: normalizedEmail,
    passwordHash: hashPassword(password),
    interests: ["ai", "automation"],
    applicationsPerDay: 3,
    resumeText: "",
    resumeFilename: "",
  };

  db.users.set(user.id, user);
  db.emailToUserId.set(normalizedEmail, user.id);

  const token = crypto.randomUUID();
  db.sessions.set(token, user.id);

  return { token, user };
}

export function login(email: string, password: string): { token: string; user: UserRecord } {
  const normalizedEmail = email.trim().toLowerCase();
  const userId = db.emailToUserId.get(normalizedEmail);
  if (!userId) throw new Error("Invalid credentials.");

  const user = db.users.get(userId);
  if (!user || user.passwordHash !== hashPassword(password)) {
    throw new Error("Invalid credentials.");
  }

  const token = crypto.randomUUID();
  db.sessions.set(token, user.id);
  return { token, user };
}

export function requireUser(token?: string | null): UserRecord {
  if (!token) throw new Error("Unauthorized");
  const userId = db.sessions.get(token);
  if (!userId) throw new Error("Unauthorized");
  const user = db.users.get(userId);
  if (!user) throw new Error("Unauthorized");
  return user;
}

export function updatePreferences(token: string, interests: string[], applicationsPerDay: number): UserRecord {
  const user = requireUser(token);
  user.interests = interests;
  user.applicationsPerDay = applicationsPerDay;
  return user;
}

export function updateResume(token: string, filename: string, text: string): UserRecord {
  const user = requireUser(token);
  user.resumeFilename = filename;
  user.resumeText = text;
  return user;
}

export function getApplications(token: string): ApplicationRecord[] {
  const user = requireUser(token);
  return db.applications.filter((item) => item.userId === user.id);
}

export function generateApplications(token: string): ApplicationRecord[] {
  const user = requireUser(token);
  const created: ApplicationRecord[] = [];

  for (let i = 0; i < user.applicationsPerDay; i += 1) {
    const interest = user.interests[i % Math.max(user.interests.length, 1)] || "general";
    const record: ApplicationRecord = {
      id: crypto.randomUUID(),
      userId: user.id,
      title: `${interest[0].toUpperCase()}${interest.slice(1)} Innovation Lead`,
      company: `Frontier Org ${i + 1}`,
      status: "notified",
      contactName: `Recruiter ${i + 1}`,
      contactEmail: `recruiter${i + 1}@frontier${i + 1}.com`,
      submittedAt: new Date().toISOString(),
    };
    db.applications.unshift(record);
    created.push(record);
  }

  return created;
}

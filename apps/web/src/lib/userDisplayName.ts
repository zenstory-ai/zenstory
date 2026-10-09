interface NamedUser {
  nickname?: string | null;
  username?: string | null;
  email?: string | null;
}

/**
 * The one name the app shows for the signed-in author: nickname, then the
 * username chosen at sign-up, then the part of the email before "@".
 * Greeting, sidebar, user menu and settings all use this rule so the same
 * person is never shown under two names.
 */
export function getUserDisplayName(user: NamedUser | null | undefined): string {
  const nickname = user?.nickname?.trim();
  if (nickname) return nickname;
  const username = user?.username?.trim();
  if (username) return username;
  const emailPrefix = user?.email?.split("@")[0]?.trim();
  return emailPrefix || "";
}

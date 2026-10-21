// Domains offered as completions while typing the email (most common first).
const EMAIL_DOMAINS = ["qq.com", "163.com", "gmail.com", "126.com", "outlook.com", "foxmail.com", "icloud.com", "hotmail.com", "sina.com", "yahoo.com"];

/** Full addresses to suggest for what has been typed so far, e.g. "lin" or "lin@g". */
export function emailSuggestions(value: string): string[] {
  const [local, domainPart, ...rest] = value.trim().split("@");
  if (!local || rest.length > 0 || /\s/.test(local)) return [];
  const typedDomain = (domainPart ?? "").toLowerCase();
  return EMAIL_DOMAINS
    .filter((domain) => domain.startsWith(typedDomain) && domain !== typedDomain)
    .map((domain) => `${local}@${domain}`);
}

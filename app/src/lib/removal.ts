// Pure helpers for researcher removal requests (safe in client components).

export interface RemovalTarget {
  id: number;
  firstName: string;
  lastName: string;
  websiteUrl?: string | null;
}

function mailto(email: string, subject: string, body: string): string {
  // encodeURIComponent, not URLSearchParams: mail clients expect %20, not "+".
  return `mailto:${email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

/** mailto: link asking to remove one researcher, or null without an address. */
export function researcherRemovalMailto(
  contactEmail: string | null,
  siteUrl: string,
  target: RemovalTarget,
): string | null {
  if (!contactEmail) return null;
  const name = `${target.firstName} ${target.lastName}`.trim();
  const lines = [
    "Please remove me from Econ Newsfeed and stop crawling my website.",
    "",
    `Name: ${name}`,
    `Researcher ID: ${target.id}`,
    `Profile: ${siteUrl}/researchers/${target.id}`,
  ];
  if (target.websiteUrl) lines.push(`Website: ${target.websiteUrl}`);
  lines.push(
    "",
    "If possible, send this from an address listed on your website or at your institution, so the request can be verified quickly.",
  );
  return mailto(contactEmail, `Removal request: ${name} (researcher #${target.id})`, lines.join("\n"));
}

/** Generic removal mailto for the privacy and contact pages. */
export function genericRemovalMailto(contactEmail: string | null): string | null {
  if (!contactEmail) return null;
  return mailto(
    contactEmail,
    "Removal request: Econ Newsfeed",
    [
      "Please remove me from Econ Newsfeed and stop crawling my website.",
      "",
      "Name:",
      "Profile link on Econ Newsfeed (if known):",
      "My website:",
    ].join("\n"),
  );
}

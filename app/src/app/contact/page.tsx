import type { Metadata } from "next";
import Link from "next/link";
import { ContactAddress, LegalPage, LegalSection, RemovalLink } from "@/components/LegalPage";
import { genericRemovalMailto } from "@/lib/removal";
import { getContactEmail, SITE_OWNER } from "@/lib/site-info";

export const metadata: Metadata = {
  title: "Contact & Impressum — Econ Newsfeed",
  description: "Who runs Econ Newsfeed and how to reach them, including removal requests.",
};

export default function ContactPage() {
  const email = getContactEmail();
  return (
    <LegalPage title="Contact & Impressum">
      <LegalSection title="Impressum (Angaben gemäß § 5 DDG)">
        <address className="not-italic">
          {SITE_OWNER.name}
          <br />
          {SITE_OWNER.street}
          <br />
          {SITE_OWNER.city}
          <br />
          {SITE_OWNER.country}
        </address>
        <p>
          Email: <ContactAddress email={email} />
        </p>
      </LegalSection>

      <LegalSection title="Verantwortlich für den Inhalt (§ 18 Abs. 2 MStV)">
        <p>
          {SITE_OWNER.name}, address as above.
        </p>
      </LegalSection>

      <LegalSection id="researchers" title="Researchers: removal from Econ Newsfeed">
        <p>
          Econ Newsfeed lists publications that economists post on their own public websites.
          If you are listed and would rather not be, email us and we will remove your profile
          and papers, delete the cached copies of your pages, stop crawling your website, and
          keep you from being added again.
        </p>
        <p>
          <RemovalLink href={genericRemovalMailto(email)}>Request removal by email</RemovalLink>.
          Every researcher page also has a &ldquo;Request removal&rdquo; link that fills in the
          details for you.
        </p>
        <p>
          Site owners can also block our crawler in <code>robots.txt</code> with{" "}
          <code>User-agent: EconNewsfeedBot</code>. It identifies itself as{" "}
          <code>EconNewsfeedBot</code> with a link to this page.
        </p>
      </LegalSection>

      <LegalSection title="Privacy">
        <p>
          How we handle personal data, and your rights, are described in the{" "}
          <Link href="/privacy" className="text-[var(--link)] hover:underline">privacy policy</Link>.
        </p>
      </LegalSection>
    </LegalPage>
  );
}

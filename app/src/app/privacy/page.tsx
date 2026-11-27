import type { Metadata } from "next";
import Link from "next/link";
import { ContactAddress, LegalPage, LegalSection, RemovalLink } from "@/components/LegalPage";
import { genericRemovalMailto } from "@/lib/removal";
import { getContactEmail, PRIVACY_LAST_UPDATED, SITE_OWNER } from "@/lib/site-info";

export const metadata: Metadata = {
  title: "Privacy — Econ Newsfeed",
  description: "Datenschutzerklärung: what Econ Newsfeed processes, why, and your rights.",
};

const list = "list-disc pl-6 space-y-1.5";

export default function PrivacyPage() {
  const email = getContactEmail();
  const removal = genericRemovalMailto(email);
  return (
    <LegalPage title="Privacy policy (Datenschutzerklärung)">
      <p className="text-sm text-[var(--text-muted)]">Last updated {PRIVACY_LAST_UPDATED}</p>

      <LegalSection title="1. Who is responsible">
        <p>
          The controller under the GDPR is {SITE_OWNER.name}, {SITE_OWNER.street},{" "}
          {SITE_OWNER.city}, {SITE_OWNER.country}. Email: <ContactAddress email={email} />. See
          also the <Link href="/contact" className="text-[var(--link)] hover:underline">Impressum</Link>.
        </p>
      </LegalSection>

      <LegalSection id="researchers" title="2. Researchers listed on Econ Newsfeed">
        <p>
          Econ Newsfeed tracks economists&rsquo; personal and departmental websites and shows new
          and updated papers in a chronological feed. For each listed researcher we process:
        </p>
        <ul className={list}>
          <li>name, position, affiliation and the short biography from their website;</li>
          <li>the addresses of their website pages, and cached copies of those pages, used to detect changes;</li>
          <li>their publications and working papers as listed there (title, co-authors, status, venue, year, links), and the history of changes to them;</li>
          <li>bibliographic metadata about those papers from OpenAlex and Crossref (DOI, abstract, co-authors);</li>
          <li>research fields (JEL codes) assigned automatically from the biography and papers.</li>
        </ul>
        <p>
          <strong>Sources:</strong> the researcher&rsquo;s own public web pages, public economics
          department and job-market directories, web search results used to find personal
          websites, OpenAlex and Crossref, and working-paper announcement newsletters.
        </p>
        <p>
          <strong>Legal basis:</strong> legitimate interests (Art. 6(1)(f) GDPR). Researchers
          publish this information so their work can be found; Econ Newsfeed makes new academic
          work easier to follow. We only use information researchers have made public about their
          professional work.
        </p>
        <p>
          <strong>Your right to object (Art. 21 GDPR):</strong> you can object at any time.{" "}
          <RemovalLink href={removal}>Request removal by email</RemovalLink>, or use the
          &ldquo;Request removal&rdquo; link on your researcher page. We then delete your profile,
          the cached copies of your pages and the papers that only you are listed for, stop
          crawling your website, and keep a minimal record (one-way hashes of your name and page
          addresses) so that you are not added again. Papers you co-authored with another listed
          researcher stay on that researcher&rsquo;s profile without a link to you. Site owners
          can also block our crawler (<code>EconNewsfeedBot</code>) in <code>robots.txt</code>.
        </p>
      </LegalSection>

      <LegalSection title="3. Accounts, follows and the weekly digest">
        <p>
          You can sign in with Google to follow researchers and receive a weekly email digest. We
          process your email address, name, Google account ID and profile picture link (from
          Google), the researchers you follow, your digest setting, and which feed items were
          already sent to you.
        </p>
        <p>
          <strong>Legal basis:</strong> providing the service you signed up for (Art. 6(1)(b) GDPR).
          You can turn the digest off in your{" "}
          <Link href="/settings" className="text-[var(--link)] hover:underline">email preferences</Link>{" "}
          or with the unsubscribe link in every digest. To delete your account, email us.
        </p>
        <p>
          <strong>Cookies:</strong> we only set cookies needed to keep you signed in (and, for
          administrators, an admin session). There is no analytics or advertising tracking
          (§ 25(2) no. 2 TDDDG).
        </p>
      </LegalSection>

      <LegalSection title="4. Server logs">
        <p>
          When you visit the site, our hosting providers process your IP address, browser
          information and the requested page to deliver the site and keep it secure (Art. 6(1)(f)
          GDPR). Logs are kept for [TO BE COMPLETED BY SITE OWNER: log retention period].
        </p>
      </LegalSection>

      <LegalSection title="5. Service providers (processors)">
        <ul className={list}>
          <li><strong>Vercel Inc.</strong> (USA): hosts the website.</li>
          <li><strong>Hetzner Online GmbH</strong> (Germany): hosts the backend, database and backups.</li>
          <li><strong>Resend</strong> (USA): delivers the weekly digest emails.</li>
          <li><strong>Google</strong> (USA/EU): Google sign-in; Google AI Studio (Gemini) reads the text of researchers&rsquo; pages to extract publication lists; Gmail receives working-paper newsletters.</li>
          <li><strong>OpenAlex</strong> (OurResearch, USA) and <strong>Crossref</strong>: lookups of paper titles, DOIs and author identifiers. No data about site users is sent.</li>
          <li><strong>Searlo</strong>: web search for researchers&rsquo; websites (researcher name and affiliation).</li>
          <li><strong>OpenAI</strong> (USA): optional automated quality checks of feed entries (paper titles and changes).</li>
        </ul>
        <p>
          Transfers to the USA rely on the EU-U.S. Data Privacy Framework or the EU Standard
          Contractual Clauses. [TO BE COMPLETED BY SITE OWNER: confirm the mechanism and
          data processing agreement for each provider.]
        </p>
      </LegalSection>

      <LegalSection title="6. How long we keep data">
        <ul className={list}>
          <li>Accounts, follows and digest settings: until you ask us to delete your account.</li>
          <li>Researcher profiles and papers: while the researcher is listed; deleted on objection as described above.</li>
          <li>Cached website copies: the latest copy and its change history, deleted with the profile.</li>
          <li>Backups: daily database backups; only the newest three are kept, so deleted data disappears from backups within a few days.</li>
        </ul>
      </LegalSection>

      <LegalSection title="7. Your rights">
        <p>
          You have the right to access (Art. 15 GDPR), rectification (Art. 16), erasure (Art. 17),
          restriction of processing (Art. 18), data portability (Art. 20) and to object
          (Art. 21). Email <ContactAddress email={email} /> to exercise them. You can also complain
          to a data protection supervisory authority (Art. 77), for example{" "}
          {SITE_OWNER.supervisoryAuthority}.
        </p>
        <p>
          We do not make automated decisions with legal or similarly significant effects about
          anyone (Art. 22). Automated tools only extract and classify publicly listed papers.
        </p>
      </LegalSection>
    </LegalPage>
  );
}

import Link from "next/link";
import { getContactEmail } from "@/lib/site-info";
import { genericRemovalMailto } from "@/lib/removal";

export default function SiteFooter() {
  const removal = genericRemovalMailto(getContactEmail());
  const linkClass = "hover:text-[var(--ink)] transition-colors";
  return (
    <footer className="border-t border-[var(--line)] mt-16">
      <nav
        aria-label="Footer"
        className="max-w-[800px] mx-auto px-6 py-6 flex flex-wrap gap-x-6 gap-y-2 font-sans text-xs text-[var(--muted)]"
      >
        <Link href="/about" className={linkClass}>About</Link>
        <Link href="/contact" className={linkClass}>Contact &amp; Impressum</Link>
        <Link href="/privacy" className={linkClass}>Privacy</Link>
        <a href={removal ?? "/privacy#researchers"} className={linkClass}>
          Researchers: request removal
        </a>
      </nav>
    </footer>
  );
}

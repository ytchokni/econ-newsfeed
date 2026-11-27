import type { ReactNode } from "react";

export function LegalPage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <article className="max-w-[800px] mx-auto px-6 py-10 font-sans text-[15px] leading-relaxed text-[var(--text-secondary)]">
      <h1 className="font-serif text-3xl font-bold text-[var(--text-primary)] mb-8">{title}</h1>
      <div className="space-y-8">{children}</div>
    </article>
  );
}

export function LegalSection({ id, title, children }: { id?: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="space-y-3 scroll-mt-8">
      <h2 className="font-serif text-xl font-semibold text-[var(--text-primary)]">{title}</h2>
      {children}
    </section>
  );
}

export function ContactAddress({ email }: { email: string | null }) {
  return email ? (
    <a href={`mailto:${email}`} className="text-[var(--link)] hover:underline">{email}</a>
  ) : (
    <span>[TO BE COMPLETED BY SITE OWNER: set CONTACT_EMAIL]</span>
  );
}

export function RemovalLink({ href, children }: { href: string | null; children: ReactNode }) {
  return href ? (
    <a href={href} className="font-medium text-[var(--link)] hover:underline">{children}</a>
  ) : (
    <span className="font-medium">{children} (contact address not configured yet)</span>
  );
}

import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Admin",
  robots: { index: false, follow: false },
};

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <style
        dangerouslySetInnerHTML={{
          __html: `
            header { display: none !important; }
            body > main { padding: 0 !important; max-width: none !important; margin: 0 !important; }
          `,
        }}
      />
      {children}
    </>
  );
}

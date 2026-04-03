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

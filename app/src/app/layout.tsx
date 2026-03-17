import type { Metadata } from "next";
import "./globals.css";
import Header from "@/components/Header";

export const metadata: Metadata = {
  title: "Econ Newsfeed",
  description:
    "Track new publications from economists' personal websites",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <Header />
        <main className="mx-auto max-w-3xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}

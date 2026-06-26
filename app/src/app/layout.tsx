import type { Metadata } from "next";
import { Source_Serif_4, DM_Sans } from "next/font/google";
import "./globals.css";
import Header from "@/components/Header";
import { AuthProvider } from "@/lib/auth";

const sourceSerif = Source_Serif_4({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--font-source-serif",
  display: "swap",
});

const dmSans = DM_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-dm-sans",
  display: "swap",
});

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
    <html lang="en" className={`${sourceSerif.variable} ${dmSans.variable}`}>
      <head>
        <link rel="alternate" type="application/atom+xml" title="Econ Newsfeed" href="/api/feed.xml" />
      </head>
      <body className="antialiased">
        <AuthProvider>
          <Header />
          <main>{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}

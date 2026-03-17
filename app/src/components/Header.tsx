import Link from "next/link";

export default function Header() {
  return (
    <header className="border-b border-gray-200 bg-white">
      <div className="mx-auto max-w-3xl px-4 py-4 flex items-center justify-between">
        <Link href="/" className="text-xl font-semibold text-gray-900">
          Econ Newsfeed
        </Link>
        <nav className="flex gap-6 text-sm">
          <Link
            href="/"
            className="text-gray-600 hover:text-gray-900 transition-colors"
          >
            Feed
          </Link>
          <Link
            href="/researchers"
            className="text-gray-600 hover:text-gray-900 transition-colors"
          >
            Researchers
          </Link>
        </nav>
      </div>
    </header>
  );
}

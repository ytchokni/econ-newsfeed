import Link from "next/link";
import type { Publication } from "@/lib/types";

function formatAuthor(author: { id: number; first_name: string; last_name: string }) {
  const initial = author.first_name.charAt(0);
  return { display: `${initial}. ${author.last_name}`, id: author.id };
}

export default function PublicationCard({
  publication,
}: {
  publication: Publication;
}) {
  const authors = publication.authors.map(formatAuthor);

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <h3 className="font-medium text-gray-900 leading-snug">
        {publication.title}
      </h3>
      <p className="mt-1 text-sm text-gray-600">
        {authors.map((a, i) => (
          <span key={a.id}>
            {i > 0 && ", "}
            <Link
              href={`/researchers/${a.id}`}
              className="hover:underline text-blue-700"
            >
              {a.display}
            </Link>
          </span>
        ))}
      </p>
      {(publication.venue || publication.year) && (
        <p className="mt-1 text-sm text-gray-500">
          {publication.venue}
          {publication.venue && publication.year && ", "}
          {publication.year}
        </p>
      )}
    </div>
  );
}

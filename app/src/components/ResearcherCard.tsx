import Link from "next/link";
import type { Researcher } from "@/lib/types";

export default function ResearcherCard({
  researcher,
}: {
  researcher: Researcher;
}) {
  return (
    <Link
      href={`/researchers/${researcher.id}`}
      className="block border border-gray-200 rounded-lg p-4 bg-white hover:border-gray-300 transition-colors"
    >
      <h3 className="font-medium text-gray-900">
        {researcher.first_name} {researcher.last_name}
      </h3>
      {(researcher.position || researcher.affiliation) && (
        <p className="mt-1 text-sm text-gray-600">
          {researcher.position}
          {researcher.position && researcher.affiliation && ", "}
          {researcher.affiliation}
        </p>
      )}
      <p className="mt-1 text-sm text-gray-500">
        {researcher.publication_count} publications tracked
      </p>
    </Link>
  );
}

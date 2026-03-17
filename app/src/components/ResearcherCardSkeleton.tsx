export default function ResearcherCardSkeleton() {
  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white animate-pulse">
      <div className="h-5 bg-gray-200 rounded w-1/2" />
      <div className="mt-2 h-4 bg-gray-200 rounded w-2/3" />
      <div className="mt-2 h-4 bg-gray-200 rounded w-1/4" />
    </div>
  );
}

export default function AffiliationLine({
  position,
  affiliation,
  className,
}: {
  position: string | null;
  affiliation: string | null;
  className?: string;
}) {
  return (
    <p className={className}>
      {position || affiliation ? (
        <>
          {position}
          {position && affiliation && ", "}
          {affiliation}
        </>
      ) : (
        <span className="text-[var(--text-muted)] italic">Affiliation unknown</span>
      )}
    </p>
  );
}

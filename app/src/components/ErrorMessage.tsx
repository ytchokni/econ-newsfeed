export default function ErrorMessage({ message }: { message: string }) {
  return (
    <div className="rounded-lg bg-rose-50 border border-rose-200 shadow-card p-4 font-sans text-sm text-rose-700">
      {message}
    </div>
  );
}

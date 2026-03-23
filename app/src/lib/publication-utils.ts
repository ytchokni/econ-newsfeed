import type { PublicationStatus } from "./types";

export const statusPillConfig: Record<PublicationStatus, { label: string; className: string }> = {
  published: { label: "Published", className: "bg-teal-100 text-teal-700" },
  working_paper: { label: "Working Paper", className: "bg-blue-100 text-blue-700" },
  revise_and_resubmit: { label: "Revise & Resubmit", className: "bg-amber-100 text-amber-700" },
  reject_and_resubmit: { label: "Reject & Resubmit", className: "bg-rose-100 text-rose-700" },
  accepted: { label: "Accepted", className: "bg-emerald-100 text-emerald-700" },
};

export function formatAuthor(author: { id: number; first_name: string; last_name: string }) {
  const initial = author.first_name.charAt(0);
  return { display: `${initial}. ${author.last_name}`, id: author.id };
}

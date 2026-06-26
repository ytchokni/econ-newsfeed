import type { PublicationStatus } from "./types";

export interface StatusStyle {
  label: string;
  className: string;
  text: string;
  bg: string;
  border: string;
}

export const statusPillConfig: Record<PublicationStatus, StatusStyle> = {
  work_in_progress: {
    label: "Work in Progress",
    className: "bg-[#F0EAF6] text-[#6A4E86] border border-[#DECFE9]",
    text: "#6A4E86", bg: "#F0EAF6", border: "#DECFE9",
  },
  working_paper: {
    label: "Working Paper",
    className: "bg-[#E5EEF3] text-[#2F5E78] border border-[#C6DCE6]",
    text: "#2F5E78", bg: "#E5EEF3", border: "#C6DCE6",
  },
  revise_and_resubmit: {
    label: "Revise & Resubmit",
    className: "bg-[#F6EFD6] text-[#8A6A12] border border-[#E8DCAE]",
    text: "#8A6A12", bg: "#F6EFD6", border: "#E8DCAE",
  },
  reject_and_resubmit: {
    label: "Reject & Resubmit",
    className: "bg-rose-100 text-rose-700 border border-rose-200",
    text: "#be123c", bg: "#ffe4e6", border: "#fecdd3",
  },
  accepted: {
    label: "Accepted",
    className: "bg-[#E4EFE8] text-[#2F6B45] border border-[#C7E0CF]",
    text: "#2F6B45", bg: "#E4EFE8", border: "#C7E0CF",
  },
  published: {
    label: "Published",
    className: "bg-[#2F6B45] text-white border border-[#2F6B45]",
    text: "#FFFFFF", bg: "#2F6B45", border: "#2F6B45",
  },
};

export function chipForStatus(label: string): { text: string; bg: string; border: string } {
  const map: Record<string, { text: string; bg: string; border: string }> = {
    "Work in Progress": statusPillConfig.work_in_progress,
    "Working Paper": statusPillConfig.working_paper,
    "Revise & Resubmit": statusPillConfig.revise_and_resubmit,
    "Reject & Resubmit": statusPillConfig.reject_and_resubmit,
    "Accepted": statusPillConfig.accepted,
    "Published": { text: "#2F6B45", bg: "#E4EFE8", border: "#C7E0CF" },
  };
  return map[label] || { text: "var(--ink2)", bg: "transparent", border: "var(--line2)" };
}

export function formatAuthor(author: { id: number; first_name: string; last_name: string }) {
  const initial = author.first_name?.charAt(0);
  return { display: initial ? `${initial}. ${author.last_name}` : author.last_name, id: author.id };
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round((today.getTime() - target.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";

  return d.toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

"use client";

import { useCallback } from "react";
import { mutate } from "swr";
import { useAuth } from "@/lib/auth";
import { useFollowing, followResearcher, unfollowResearcher } from "@/lib/api";

export default function FollowButton({
  researcherId,
  size = "sm",
}: {
  researcherId: number;
  size?: "sm" | "md";
}) {
  const { isAuthenticated, accessToken } = useAuth();
  const { data } = useFollowing(accessToken);

  const isFollowing = data?.researcher_ids?.includes(researcherId) ?? false;

  const handleClick = useCallback(
    async (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!accessToken) return;

      const key = ["/api/users/following", accessToken];
      const currentIds = data?.researcher_ids ?? [];

      // Optimistic update
      const newIds = isFollowing
        ? currentIds.filter((id) => id !== researcherId)
        : [...currentIds, researcherId];
      mutate(key, { researcher_ids: newIds }, false);

      try {
        if (isFollowing) {
          await unfollowResearcher(researcherId, accessToken);
        } else {
          await followResearcher(researcherId, accessToken);
        }
        mutate(key);
      } catch {
        mutate(key);
      }
    },
    [accessToken, data, isFollowing, researcherId],
  );

  if (!isAuthenticated) return null;

  const sizeClasses =
    size === "md"
      ? "px-4 py-1.5 text-sm"
      : "px-2.5 py-0.5 text-xs";

  return (
    <button
      onClick={handleClick}
      className={`relative z-[1] font-sans font-semibold rounded-full transition-all ${sizeClasses} ${
        isFollowing
          ? "bg-[var(--accent)] text-white hover:bg-red-600"
          : "border border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
      }`}
    >
      {isFollowing ? "Following" : "Follow"}
    </button>
  );
}

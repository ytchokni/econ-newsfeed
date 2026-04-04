"use client";

import { useState, useCallback } from "react";
import { useAdminDashboard } from "@/lib/api";
import LoginForm from "./LoginForm";
import HealthTab from "./tabs/HealthTab";
import ContentTab from "./tabs/ContentTab";
import QualityTab from "./tabs/QualityTab";
import CostsTab from "./tabs/CostsTab";
import ScrapesTab from "./tabs/ScrapesTab";
import ActivityTab from "./tabs/ActivityTab";

const TABS = [
  { id: "health", label: "Health" },
  { id: "content", label: "Content" },
  { id: "quality", label: "Quality" },
  { id: "costs", label: "Costs" },
  { id: "scrapes", label: "Scrapes" },
  { id: "activity", label: "Activity" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function AdminDashboard() {
  const [activeTab, setActiveTab] = useState<TabId>("health");
  const { data, error, isLoading, mutate } = useAdminDashboard();
  const [loggedOut, setLoggedOut] = useState(false);

  const isUnauthorized = loggedOut || error?.message === "UNAUTHORIZED";

  const handleLoginSuccess = useCallback(() => {
    setLoggedOut(false);
    mutate();
  }, [mutate]);

  if (isLoading && !data) {
    return (
      <div className="min-h-screen bg-[#0f1117] flex items-center justify-center">
        <p className="text-zinc-500 font-[family-name:var(--font-dm-sans)]">Loading…</p>
      </div>
    );
  }

  if (isUnauthorized) {
    return <LoginForm onSuccess={handleLoginSuccess} />;
  }

  async function handleLogout() {
    await fetch("/api/admin/logout", { method: "POST" });
    setLoggedOut(true);
  }

  return (
    <div className="min-h-screen bg-[#0f1117] text-zinc-100 font-[family-name:var(--font-dm-sans)]">
      {/* Header */}
      <div className="border-b border-[#2a2d3a] px-6 py-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Admin Dashboard</h1>
        <button
          onClick={handleLogout}
          className="text-sm text-zinc-500 hover:text-zinc-300"
        >
          Sign out
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-[#2a2d3a] px-6">
        <nav className="flex gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.id
                  ? "border-[#4a9eff] text-zinc-100"
                  : "border-transparent text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab content */}
      <div className="p-6 max-w-6xl mx-auto">
        {isLoading && !data ? (
          <p className="text-zinc-500">Loading…</p>
        ) : error && error.message !== "UNAUTHORIZED" ? (
          <p className="text-red-400">Error loading dashboard data</p>
        ) : data ? (
          <>
            {activeTab === "health" && <HealthTab data={data.health} />}
            {activeTab === "content" && <ContentTab data={data.content} />}
            {activeTab === "quality" && (
              <QualityTab data={data.quality} totalPapers={data.content.total_papers} totalResearchers={data.content.total_researchers} />
            )}
            {activeTab === "costs" && <CostsTab data={data.costs} />}
            {activeTab === "scrapes" && <ScrapesTab data={data.scrapes} />}
            {activeTab === "activity" && <ActivityTab data={data.activity} />}
          </>
        ) : null}
      </div>
    </div>
  );
}

import { Metadata } from "next";
import AdminDashboard from "./AdminDashboard";

export const metadata: Metadata = {
  title: "Admin Dashboard — Econ Newsfeed",
  robots: "noindex, nofollow",
};

export default function AdminPage() {
  return <AdminDashboard />;
}

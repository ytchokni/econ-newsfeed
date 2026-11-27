import { redirect } from "next/navigation";

// German visitors often look for /impressum; the content lives on /contact.
export default function ImpressumPage() {
  redirect("/contact");
}

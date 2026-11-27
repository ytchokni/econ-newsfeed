import { serializeJsonLd } from "@/lib/seo";

/** Structured data for search engines (not executed, so CSP does not apply). */
export default function JsonLd({ data }: { data: unknown }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: serializeJsonLd(data) }}
    />
  );
}

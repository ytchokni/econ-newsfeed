import { Suspense } from "react";
import NewsfeedContent from "./NewsfeedContent";

export default function Home() {
  return (
    <Suspense>
      <NewsfeedContent />
    </Suspense>
  );
}

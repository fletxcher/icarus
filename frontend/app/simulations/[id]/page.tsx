import { Suspense } from "react";
import { SimulationPage } from "@/components/chat/simulation-detail";

// The id is only known per request, so it is read inside Suspense: the rest of the page can
// still be prerendered (Cache Components).
export default function Page({ params }: { params: Promise<{ id: string }> }) {
  return (
    <Suspense fallback={null}>
      <Simulation params={params} />
    </Suspense>
  );
}

async function Simulation({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SimulationPage jobId={id} />;
}

import { redirect } from "next/navigation";

const viewToTab: Record<string, string> = {
  graph: "Historical",
  historical: "Historical",
  snapshot: "Summary",
  performance: "Performance",
  forecast: "Forecasting",
  forecasting: "Forecasting",
  financials: "Financials",
  consensus: "Consensus",
  peers: "Peers",
  audit: "Data Audit"
};

export default async function CompanyViewPage({
  params
}: {
  params: Promise<{ id: string; view: string }>;
}) {
  const { id, view } = await params;
  const tab = viewToTab[view.toLowerCase()] ?? "Historical";
  redirect(`/terminal?ticker=${encodeURIComponent(id)}&tab=${encodeURIComponent(tab)}`);
}

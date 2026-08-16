import { redirect } from "next/navigation";

export default async function CompanyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/terminal?ticker=${encodeURIComponent(id)}&tab=Historical`);
}

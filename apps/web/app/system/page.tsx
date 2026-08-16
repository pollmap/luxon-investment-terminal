import { redirect } from "next/navigation";

export default function SystemPage() {
  redirect("/terminal?tab=System");
}

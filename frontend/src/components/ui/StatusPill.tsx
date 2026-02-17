import { Badge } from "@/components/ui/Badge";

type StatusPillProps = {
  status: string;
};

function normalizeStatus(status: string) {
  return status.trim().toLowerCase();
}

function statusVariant(status: string): "default" | "success" | "warning" | "danger" | "info" {
  const normalized = normalizeStatus(status);

  if (["submitted", "applied", "processing", "in review"].includes(normalized)) return "info";
  if (["interview", "offer", "hired", "completed", "success"].includes(normalized)) return "success";
  if (["queued", "pending", "draft"].includes(normalized)) return "warning";
  if (["rejected", "failed", "error", "declined"].includes(normalized)) return "danger";
  return "default";
}

export function StatusPill({ status }: StatusPillProps) {
  return <Badge variant={statusVariant(status)}>{status}</Badge>;
}

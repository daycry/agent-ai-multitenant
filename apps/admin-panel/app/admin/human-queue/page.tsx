// task_ui_03: la cola humana (ADR 0123) vive ahora en la pestaña Aprobaciones de la
// bandeja unificada, bajo «Otras decisiones pendientes».
import { redirect } from "next/navigation";

export default function HumanQueuePage() {
  redirect("/admin/inbox?tab=approvals");
}

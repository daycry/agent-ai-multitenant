// task_ui_03: la ruta sigue respondiendo y lleva a la pestaña Aprobaciones de la bandeja
// unificada (las solicitudes de acción sensible y las preguntas ask_human).
import { redirect } from "next/navigation";

export default function ApprovalsPage() {
  redirect("/admin/inbox?tab=approvals");
}

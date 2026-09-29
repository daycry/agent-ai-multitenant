// task_ui_03: el histórico de notificaciones es la pestaña Notificaciones de la bandeja
// unificada (components/inbox/notifications-panel.tsx).
import { redirect } from "next/navigation";

export default function NotificationsInboxPage() {
  redirect("/admin/inbox?tab=notifications");
}

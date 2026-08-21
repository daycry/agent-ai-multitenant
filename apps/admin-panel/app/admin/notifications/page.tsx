"use client";

/**
 * Plan 10 task_10_15 — UI de configuración de notificaciones en 3 capas.
 *
 * Tres pestañas que mapean los tres scopes del modelo (CLAUDE.md §6,
 * plataforma → tenant → usuario):
 *
 *   - **Plataforma** (solo System Admin): qué transportes (Telegram, Email,
 *     Slack, …) están habilitados globalmente. Un tenant solo puede
 *     configurar canales de un transporte habilitado aquí.
 *   - **Canales** (Tenant Admin): canales concretos del tenant / del propio
 *     admin (scope tenant|user), con su secreto. El secreto se cifra en
 *     reposo en el backend y NUNCA se devuelve: la UI solo sabe si hay
 *     secreto (`has_secret` + `secret_source`).
 *   - **Preferencias** (Tenant Admin): reglas de enrutado evento→canal
 *     (opt-in/out, horas de silencio) — el primitivo del test human_10_02.
 *
 * Permisos: el backend es la fuente de verdad (RBAC por scope + RLS). La UI
 * envuelve las acciones de escritura en <RoleGuard> y oculta la pestaña de
 * plataforma a quien no sea System Admin, pero nunca confía solo en eso.
 *
 * prod-16 `task_prod16_08`: esto eran 831 líneas con las tres pestañas, el
 * diálogo de canal y la matriz dentro. Cada pestaña vive ahora en su fichero
 * (`platform-tab`, `channels-tab`, `preferences-tab`) sobre los tipos comunes
 * de `notification-types`. El troceo es mecánico y lo vigila `page.test.tsx`,
 * que se escribió ANTES de mover una línea y no ha cambiado una aserción.
 *
 * Endpoints (routers/notifications.py):
 *   GET    /notifications/platform/channel-types   (lectura: cualquier miembro)
 *   PUT    /notifications/platform/channel-types   (System Admin)
 *   GET    /notifications/channels
 *   POST   /notifications/channels
 *   PUT    /notifications/channels/{id}
 *   DELETE /notifications/channels/{id}
 *   GET    /notifications/preferences
 *   PUT    /notifications/preferences
 *   DELETE /notifications/preferences/{id}
 */

import { Bell } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";
import { useCurrentUser } from "@/lib/use-current-user";

import { ChannelsTab } from "./channels-tab";
import { PlatformTab } from "./platform-tab";
import { PreferencesTab } from "./preferences-tab";

export default function NotificationConfigPage() {
  const t = useT("notifications");
  const { isSystemAdmin } = useCurrentUser();

  return (
    <div
      className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="notification-config-page"
    >
      <PageHeader
        icon={<Bell className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="notification-config-header"
      />

      <Tabs defaultValue="channels" className="mt-6">
        <TabsList data-testid="notification-tabs">
          <TabsTrigger value="channels" data-testid="tab-channels">
            {t("tabChannels")}
          </TabsTrigger>
          <TabsTrigger value="preferences" data-testid="tab-preferences">
            {t("tabPreferences")}
          </TabsTrigger>
          {isSystemAdmin ? (
            <TabsTrigger value="platform" data-testid="tab-platform">
              {t("tabPlatform")}
            </TabsTrigger>
          ) : null}
        </TabsList>

        <TabsContent value="channels">
          <ChannelsTab />
        </TabsContent>
        <TabsContent value="preferences">
          <PreferencesTab />
        </TabsContent>
        {isSystemAdmin ? (
          <TabsContent value="platform">
            <PlatformTab />
          </TabsContent>
        ) : null}
      </Tabs>
    </div>
  );
}

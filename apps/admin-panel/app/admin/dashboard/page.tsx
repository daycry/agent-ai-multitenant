"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";
import { clearToken } from "@/lib/auth";

interface ServiceHealth {
  name: string;
  status: "ok" | "degraded" | "down" | string;
  detail?: string | null;
}

interface SystemHealthResponse {
  status: string;
  services: ServiceHealth[];
}

const statusClasses: Record<string, string> = {
  ok: "bg-green-100 text-green-800",
  degraded: "bg-yellow-100 text-yellow-800",
  down: "bg-red-100 text-red-800",
};

export default function DashboardPage() {
  const router = useRouter();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["system-health"],
    queryFn: () => apiFetch<SystemHealthResponse>("/admin/system-health"),
    // The plan requires a 30-second refresh on the dashboard.
    refetchInterval: 30_000,
    retry: 1,
  });

  async function onLogout() {
    try {
      await apiFetch<void>("/auth/logout", { method: "POST" });
    } catch {
      // Token may already be invalid; we still wipe local state.
    } finally {
      clearToken();
      router.replace("/login");
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-5xl px-4 py-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">System Health</h1>
          <p className="text-muted-foreground text-sm">Auto-refreshing every 30 s.</p>
        </div>
        <Button variant="outline" onClick={onLogout} data-testid="logout">
          Sign out
        </Button>
      </header>

      {isLoading && <p className="text-muted-foreground text-sm">Loading services…</p>}

      {isError && (
        <Card className="border-destructive p-4" data-testid="dashboard-error">
          <p className="text-destructive text-sm">
            Could not load services: {error instanceof ApiError ? error.body : String(error)}
          </p>
        </Card>
      )}

      {data && (
        <section
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
          data-testid="services-grid"
        >
          {data.services.map((service) => (
            <Card key={service.name} data-testid={`service-${service.name}`}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-base">{service.name}</CardTitle>
                <span
                  className={cn(
                    "rounded px-2 py-0.5 text-xs font-medium",
                    statusClasses[service.status] ?? "bg-muted text-muted-foreground",
                  )}
                  data-testid={`service-${service.name}-status`}
                >
                  {service.status}
                </span>
              </CardHeader>
              {service.detail && (
                <CardContent>
                  <p className="text-muted-foreground text-xs">{service.detail}</p>
                </CardContent>
              )}
            </Card>
          ))}
        </section>
      )}
    </main>
  );
}

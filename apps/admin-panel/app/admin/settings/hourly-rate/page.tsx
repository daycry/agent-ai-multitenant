"use client";

/**
 * task_03_26 — Tarifa horaria del tenant.
 *
 * Pantalla del panel admin donde un `tenant_admin` configura la
 * tarifa horaria que usa el calculador de coste humano (task_03_22).
 * NULL = se cae al valor por defecto de plataforma (50 €/h).
 *
 * Permisos: cualquier usuario autenticado con tenant_id en el JWT
 * puede LEER; sólo `tenant_admin` puede PERSISTIR (el backend
 * devuelve 403 si no).
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Coins } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch } from "@/lib/api";

interface HourlyRateResponse {
  hourly_rate: string | null;
  hourly_rate_currency: string | null;
}

export default function HourlyRatePage() {
  const queryClient = useQueryClient();
  const [rate, setRate] = useState("");
  const [currency, setCurrency] = useState("EUR");

  const settingsQuery = useQuery({
    queryKey: ["tenant-hourly-rate"],
    queryFn: () => apiFetch<HourlyRateResponse>("/tenant-settings/hourly-rate"),
    refetchOnWindowFocus: false,
  });

  // Seed the form once the GET returns.
  useEffect(() => {
    const data = settingsQuery.data;
    if (!data) return;
    if (data.hourly_rate !== null) setRate(data.hourly_rate);
    if (data.hourly_rate_currency !== null) setCurrency(data.hourly_rate_currency);
  }, [settingsQuery.data]);

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<HourlyRateResponse>("/tenant-settings/hourly-rate", {
        method: "PUT",
        body: {
          hourly_rate: rate || null,
          hourly_rate_currency: currency || null,
        },
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["tenant-hourly-rate"], data);
      // Also bust the cost-breakdown caches — they depend on this.
      queryClient.invalidateQueries({ queryKey: ["plan-cost-breakdown"] });
    },
  });

  const isDirty =
    settingsQuery.data &&
    (rate !== (settingsQuery.data.hourly_rate ?? "") ||
      currency !== (settingsQuery.data.hourly_rate_currency ?? "EUR"));

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Coins className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Tarifa horaria del tenant"
        description="Multiplicador que el cálculo de coste humano (planes) usa por defecto. Si lo dejas vacío, se aplica el valor por defecto de plataforma (50 EUR/h)."
        data-testid="hourly-rate-header"
      />

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Configuración</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {settingsQuery.isLoading ? (
            <p className="text-muted-foreground text-sm">Cargando…</p>
          ) : (
            <form
              className="space-y-4"
              data-testid="hourly-rate-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (!mutation.isPending) mutation.mutate();
              }}
            >
              <div>
                <Label htmlFor="hourly-rate-input">Tarifa por hora</Label>
                <Input
                  id="hourly-rate-input"
                  data-testid="hourly-rate-input"
                  type="number"
                  step="0.01"
                  min="0"
                  max="10000"
                  value={rate}
                  onChange={(e) => setRate(e.target.value)}
                  placeholder="50.00"
                />
              </div>
              <div>
                <Label htmlFor="hourly-rate-currency-input">Moneda</Label>
                <Input
                  id="hourly-rate-currency-input"
                  data-testid="hourly-rate-currency-input"
                  type="text"
                  maxLength={3}
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value.toUpperCase())}
                  placeholder="EUR"
                />
              </div>
              {mutation.isError ? (
                <p className="text-destructive text-xs" data-testid="hourly-rate-error">
                  {mutation.error instanceof ApiError
                    ? mutation.error.body
                    : String(mutation.error)}
                </p>
              ) : mutation.isSuccess ? (
                <p className="text-emerald-600 text-xs" data-testid="hourly-rate-saved">
                  Guardado.
                </p>
              ) : null}
              <div className="flex justify-end">
                <Button
                  type="submit"
                  disabled={!isDirty || mutation.isPending}
                  data-testid="hourly-rate-submit"
                >
                  Guardar
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { getToken } from "@/lib/auth";

/**
 * Client-side auth gate. Phase 0 only — phase 15 will move to a
 * middleware-level redirect once we use httpOnly cookies that the
 * Next.js edge can read at request time.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setReady(true);
  }, [router]);

  if (!ready) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-muted-foreground text-sm">Loading…</p>
      </main>
    );
  }

  return <>{children}</>;
}

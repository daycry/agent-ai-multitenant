// @vitest-environment jsdom
//
// Pantalla de canje de invitación (ADR 0134, opción C).
//
// Las cuatro propiedades que hacen que esta pantalla sirva para algo, y que sin
// test se caen en silencio:
//
//   1. El token llega en la URL (`?token=…`) y la pantalla lo lleva al
//      formulario, para que el invitado no tenga que copiar y pegar un secreto
//      de 50 caracteres.
//   2. El token se envía en el CUERPO de `POST /auth/register`, nunca como
//      query string — un secreto en la URL acaba en los logs de acceso y en el
//      `Referer`. Es la misma regla que el repo ya fijó para `X-API-Token`.
//   3. Un 403 se traduce a "pide otra invitación" y NO a un motivo inventado:
//      el backend devuelve el mismo 403 para caducada, revocada, ya canjeada y
//      "para otro email" a propósito, porque distinguirlos reabriría el oráculo
//      de enumeración que el ADR cerró. Si la UI se inventara el motivo, estaría
//      afirmando algo que no sabe.
//   4. Al terminar NO se inicia sesión sola: el registro no acuña sesión, así
//      que la pantalla lleva al login.

import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

const pushMock = vi.fn();
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: pushMock }),
  useSearchParams: () => searchParams,
}));

import { ApiError } from "@/lib/api";
import AcceptInvitePage from "@/app/accept-invite/page";

configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  pushMock.mockReset();
  searchParams = new URLSearchParams();
});

function fillAndSubmit({ email = "invitee@example.com" } = {}) {
  fireEvent.change(screen.getByTestId("accept-invite-email"), { target: { value: email } });
  fireEvent.change(screen.getByTestId("accept-invite-password"), {
    target: { value: "longenoughpw" },
  });
  fireEvent.submit(screen.getByTestId("accept-invite-form"));
}

describe("Canje de invitación", () => {
  it("precarga el token de la URL en el formulario", () => {
    searchParams = new URLSearchParams("token=aainv_deadbeef_secret-tail");
    render(<AcceptInvitePage />);
    const field = screen.getByTestId("accept-invite-token") as HTMLInputElement;
    expect(field.value).toBe("aainv_deadbeef_secret-tail");
  });

  it("manda el token en el CUERPO, no en la query string", async () => {
    searchParams = new URLSearchParams("token=aainv_deadbeef_secret-tail");
    apiFetchMock.mockResolvedValue({ id: "u-1", email: "invitee@example.com" });
    render(<AcceptInvitePage />);
    fillAndSubmit();

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled());
    const [path, opts] = apiFetchMock.mock.calls[0] as [string, { method: string; body: unknown }];
    // La ruta va limpia: ni `?invite=`, ni `?token=`.
    expect(path).toBe("/auth/register");
    expect(path).not.toContain("?");
    expect(opts.method).toBe("POST");
    expect(opts.body).toEqual({
      email: "invitee@example.com",
      password: "longenoughpw",
      full_name: null,
      invitation_token: "aainv_deadbeef_secret-tail",
    });
  });

  it("tras el alta lleva al login y NO deja al usuario a medias", async () => {
    searchParams = new URLSearchParams("token=t");
    apiFetchMock.mockResolvedValue({ id: "u-1", email: "invitee@example.com" });
    render(<AcceptInvitePage />);
    fillAndSubmit();

    await waitFor(() => expect(screen.getByTestId("accept-invite-success")).toBeTruthy());
    fireEvent.click(screen.getByTestId("accept-invite-go-login"));
    expect(pushMock).toHaveBeenCalledWith("/login");
  });

  it("un 403 se traduce SIEMPRE al mismo texto, sea cual sea el cuerpo del backend", async () => {
    // La propiedad que importa: la pantalla no repinta el `detail` del backend.
    // El 403 es genérico a propósito (todos los motivos comparten respuesta para
    // no reabrir el oráculo de enumeración); si la UI mostrara el cuerpo crudo,
    // bastaría con que alguien afinara ese `detail` en el servidor para que el
    // oráculo volviera por la puerta de atrás sin que nadie lo notara aquí.
    searchParams = new URLSearchParams("token=t");

    apiFetchMock.mockRejectedValue(
      new ApiError(403, '{"detail":"registration is by invitation only"}'),
    );
    const first = render(<AcceptInvitePage />);
    fillAndSubmit();
    await waitFor(() => expect(screen.getByTestId("accept-invite-error")).toBeTruthy());
    const generic = screen.getByTestId("accept-invite-error").textContent ?? "";
    first.unmount();

    apiFetchMock.mockRejectedValue(
      new ApiError(403, '{"detail":"invitation expired for ana@x.io"}'),
    );
    render(<AcceptInvitePage />);
    fillAndSubmit();
    await waitFor(() => expect(screen.getByTestId("accept-invite-error")).toBeTruthy());
    const leaky = screen.getByTestId("accept-invite-error").textContent ?? "";

    expect(generic).not.toBe("");
    expect(leaky).toBe(generic);
    // Y nada del cuerpo del backend se ha colado en pantalla.
    expect(leaky).not.toContain("ana@x.io");
    expect(leaky).toContain("Pide una nueva al administrador");
  });

  it("un 409 dice que la cuenta ya existe (ahí sí hay motivo conocido)", async () => {
    searchParams = new URLSearchParams("token=t");
    apiFetchMock.mockRejectedValue(new ApiError(409, '{"detail":"email already registered"}'));
    render(<AcceptInvitePage />);
    fillAndSubmit();

    await waitFor(() => expect(screen.getByTestId("accept-invite-error")).toBeTruthy());
    expect(screen.getByTestId("accept-invite-error").textContent).toContain("Ya existe una cuenta");
  });
});

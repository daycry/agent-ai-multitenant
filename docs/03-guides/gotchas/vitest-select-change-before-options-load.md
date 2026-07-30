---
title: "`fireEvent.change` sobre un `<select>` cuyas `<option>` aún no han cargado no hace nada — y en silencio"
area: tests, frontend
encountered: 2026-07-25
stack: vitest + @testing-library/react, TanStack Query
---

## Síntoma

Un test rellena un formulario y pulsa Enviar, pero el botón sigue deshabilitado o
la mutación nunca se dispara. El mismo test con los mismos datos pasa si se le
añade cualquier `await` antes.

```
Unable to find an element by: [data-testid="launch-eval-run-done"]
```

## Causa raíz

El `<select>` se llena desde una query asíncrona. Si el `change` llega antes de
que existan las `<option>`, el DOM **descarta el valor**: un `<select>` no puede
tomar un value que no corresponde a ninguna opción. No hay aviso ni excepción —
`e.target.value` queda `""` y el estado del formulario se queda vacío.

Es asimétrico y por eso despista: un `<input>` sí acepta cualquier valor, así que
los campos de texto del mismo formulario funcionan y solo falla el desplegable.

## Fix

Esperar a que las opciones estén pintadas antes de seleccionar:

```tsx
await waitFor(() => expect(screen.getByText(/dorado \(12 items\)/)).toBeTruthy());
fireEvent.change(screen.getByTestId("launch-eval-run-dataset"), {
  target: { value: "d-full" },
});
```

## Cómo verificar el fix

Afirmar el valor justo después del `change`:
`expect((screen.getByTestId("…") as HTMLSelectElement).value).toBe("d-full")`.
Si sale `""`, las opciones aún no estaban.

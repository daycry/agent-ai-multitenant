// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { HydrationMarker } from "@/components/layout/hydration-marker";

afterEach(cleanup);

describe("HydrationMarker", () => {
  it("marca <html data-hydrated> al montar y lo retira al desmontar", () => {
    expect(document.documentElement.getAttribute("data-hydrated")).toBeNull();
    const { unmount } = render(<HydrationMarker />);
    expect(document.documentElement.getAttribute("data-hydrated")).toBe("true");
    unmount();
    expect(document.documentElement.getAttribute("data-hydrated")).toBeNull();
  });
});

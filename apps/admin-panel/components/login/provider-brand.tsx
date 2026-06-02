"use client";

/**
 * Official brand logos + sign-in button styling for the global auth
 * providers shown on `/login` (ADR 0047, task_sso_05).
 *
 * Auth providers are PLATFORM-GLOBAL: the public `GET /auth/sso/providers`
 * endpoint returns each enabled provider's `kind` (`oidc` / `saml`) plus an
 * optional operator-set `button_label`. We map the provider to a BRAND by
 * inspecting the `kind` and the operator label/display name (Microsoft,
 * Google, GitHub all have strict, official sign-in button guidelines we
 * follow: correct logo, recommended colors, recommended text). Anything we
 * cannot confidently brand falls back to a neutral OIDC/SAML treatment.
 *
 * The logos below are the providers' OWN published marks (simple-icons /
 * official brand pages), inlined as SVG so the button renders offline with
 * no extra request. Microsoft's logo is the four-square mark; the "Sign in
 * with Microsoft" text + light button is per the Microsoft brand guidance.
 * Google follows the "G" mark on a white button (neutral light theme).
 * GitHub uses the Octocat mark on its near-black brand color.
 */

import type { ComponentType, SVGProps } from "react";

/** The brands we render with a first-class, guideline-compliant treatment. */
export type ProviderBrand = "microsoft" | "google" | "github" | "oidc" | "saml";

interface BrandSpec {
  /** Default sign-in text when the operator left `button_label` unset. */
  defaultLabel: string;
  /** The brand mark. */
  Logo: ComponentType<SVGProps<SVGSVGElement>>;
  /**
   * Tailwind classes for the button surface. Each brand follows its own
   * official guidance (light surface for Microsoft/Google, dark for
   * GitHub); the generic OIDC/SAML reuse the app's outline style.
   */
  className: string;
}

// --------------------------------------------------------------------------
// Brand marks (official logos, inlined as SVG)
// --------------------------------------------------------------------------
function MicrosoftLogo(props: SVGProps<SVGSVGElement>) {
  // Microsoft's four-square logo (no recoloring — the squares are fixed
  // brand colors). aria-hidden: the button text carries the label.
  return (
    <svg viewBox="0 0 21 21" role="img" aria-hidden="true" {...props}>
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

function GoogleLogo(props: SVGProps<SVGSVGElement>) {
  // Google's "G" mark (the four brand colors). Per Google's guidance the
  // logo keeps its colors on a neutral (white) button surface.
  return (
    <svg viewBox="0 0 48 48" role="img" aria-hidden="true" {...props}>
      <path
        fill="#ffc107"
        d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"
      />
      <path
        fill="#ff3d00"
        d="m6.306 14.691 6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"
      />
      <path
        fill="#4caf50"
        d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238A11.91 11.91 0 0 1 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z"
      />
      <path
        fill="#1976d2"
        d="M43.611 20.083H42V20H24v8h11.303a12.04 12.04 0 0 1-4.087 5.571l.003-.002 6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"
      />
    </svg>
  );
}

function GitHubLogo(props: SVGProps<SVGSVGElement>) {
  // GitHub's Octocat mark, drawn in currentColor so it reads white on the
  // dark brand button.
  return (
    <svg viewBox="0 0 16 16" role="img" aria-hidden="true" fill="currentColor" {...props}>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

function OidcLogo(props: SVGProps<SVGSVGElement>) {
  // Generic identity mark for an un-branded OIDC provider (key glyph).
  return (
    <svg viewBox="0 0 24 24" role="img" aria-hidden="true" fill="none" {...props}>
      <path
        d="M15.5 7.5a4 4 0 1 0-3.9 4l-3.1 3.1V17H6v2H4v-2.6l5.6-5.6A4 4 0 0 1 15.5 7.5Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <circle cx="14.5" cy="7" r="1.3" fill="currentColor" />
    </svg>
  );
}

function SamlLogo(props: SVGProps<SVGSVGElement>) {
  // Generic shield mark for an un-branded SAML provider.
  return (
    <svg viewBox="0 0 24 24" role="img" aria-hidden="true" fill="none" {...props}>
      <path
        d="M12 3 5 5.5V11c0 4.2 2.9 8 7 9.5 4.1-1.5 7-5.3 7-9.5V5.5L12 3Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="m9 11.8 2 2L15 9.8"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const BRAND_SPECS: Record<ProviderBrand, BrandSpec> = {
  // Microsoft brand guidance: white button, neutral border, the colored
  // four-square mark, "Sign in with Microsoft".
  microsoft: {
    defaultLabel: "Sign in with Microsoft",
    Logo: MicrosoftLogo,
    className:
      "border border-[#8c8c8c] bg-white text-[#5e5e5e] hover:bg-[#f3f3f3] focus-visible:ring-[#5e5e5e]/40",
  },
  // Google brand guidance: light (white) button with a subtle border and
  // the colored "G" mark, "Sign in with Google".
  google: {
    defaultLabel: "Sign in with Google",
    Logo: GoogleLogo,
    className:
      "border border-[#dadce0] bg-white text-[#3c4043] hover:bg-[#f8f9fa] focus-visible:ring-[#4285f4]/40",
  },
  // GitHub brand guidance: the near-black brand color with the white
  // Octocat, "Sign in with GitHub".
  github: {
    defaultLabel: "Sign in with GitHub",
    Logo: GitHubLogo,
    className:
      "border border-[#1b1f24] bg-[#24292f] text-white hover:bg-[#1b1f24] focus-visible:ring-white/40",
  },
  // Neutral fallbacks reuse the app's outline treatment so an un-branded
  // IdP still looks at home in the panel.
  oidc: {
    defaultLabel: "Sign in with SSO",
    Logo: OidcLogo,
    className:
      "border-input bg-background text-foreground hover:bg-muted border focus-visible:ring-ring",
  },
  saml: {
    defaultLabel: "Sign in with SSO",
    Logo: SamlLogo,
    className:
      "border-input bg-background text-foreground hover:bg-muted border focus-visible:ring-ring",
  },
};

/**
 * Resolve a provider (its `kind` + any operator hint) to a BRAND.
 *
 * The backend `kind` is only `oidc` / `saml`, so the specific brand
 * (Microsoft / Google / GitHub) is inferred from the operator's
 * `button_label` / `display_name` — the operator names the provider when
 * they configure it ("Sign in with Microsoft", "Acme Google Workspace",
 * …). A SAML provider always uses the SAML fallback unless the label names
 * a known brand; an OIDC one that names no known brand uses the OIDC
 * fallback. Matching is accent-insensitive lower-cased substring.
 */
export function resolveBrand(kind: string, ...hints: (string | null | undefined)[]): ProviderBrand {
  const haystack = hints
    .filter((h): h is string => Boolean(h))
    .join(" ")
    .toLowerCase();
  if (/\b(microsoft|entra|azure|office\s?365|o365)\b/.test(haystack)) return "microsoft";
  if (/\b(google|gsuite|g\s?suite|workspace)\b/.test(haystack)) return "google";
  if (/\bgithub\b/.test(haystack)) return "github";
  return kind === "saml" ? "saml" : "oidc";
}

export function brandSpec(brand: ProviderBrand): {
  defaultLabel: string;
  Logo: ComponentType<SVGProps<SVGSVGElement>>;
  className: string;
} {
  return BRAND_SPECS[brand];
}

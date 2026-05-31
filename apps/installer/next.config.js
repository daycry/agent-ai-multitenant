/**
 * @type {import('next').NextConfig}
 *
 * The installer UI is temporary and self-destructing (Plan 15 Fase A): it is
 * served only while provisioning, then the container is torn down. We still
 * use `output: 'standalone'` so the bootstrap image stays small.
 *
 * `NEXT_PUBLIC_INSTALLER_API` points the wizard at the FastAPI backend; in the
 * bootstrap compose both run in the same container network.
 */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,
};

module.exports = nextConfig;

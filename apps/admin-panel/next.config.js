/**
 * @type {import('next').NextConfig}
 *
 * `output: 'standalone'` keeps the prod image small — Next emits a
 * self-contained server bundle plus a minimal node_modules tree.
 * The Dockerfile in phase 12 will COPY only that.
 */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,
};

module.exports = nextConfig;

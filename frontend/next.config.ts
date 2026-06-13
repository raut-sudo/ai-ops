import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required for the Docker standalone build (copies only the files needed
  // to serve the app — no node_modules in the final image).
  output: "standalone",

  // Proxy /api/v1/* to the backend — avoids CORS entirely in all envs.
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendUrl}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;

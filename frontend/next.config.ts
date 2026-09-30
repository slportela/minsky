import type { NextConfig } from "next";

// "standalone" produces a minimal server bundle for the Docker image.
const nextConfig: NextConfig = { output: "standalone" };

export default nextConfig;

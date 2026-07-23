/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The profile is injected at build time by `push.py`, so each prospect's
  // deploy is a static page that renders their brand with no request in front
  // of it. A cold serverless call before the practice name appears is the one
  // thing that would make the demo feel like a prototype.
  env: {
    NEXT_PUBLIC_PROSPECT_PROFILE: process.env.NEXT_PUBLIC_PROSPECT_PROFILE ?? "",
    NEXT_PUBLIC_WEBHOOK_BASE_URL:
      process.env.NEXT_PUBLIC_WEBHOOK_BASE_URL ?? "http://localhost:8000",
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "",
  },
};

export default nextConfig;

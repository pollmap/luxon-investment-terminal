const staticExport = process.env.MARKET_WEB_OUTPUT === "static";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: staticExport ? "export" : "standalone",
  distDir: process.env.NEXT_BUILD_DIR ?? ".next",
  allowedDevOrigins: ["127.0.0.1"],
  transpilePackages: [],
  ...(staticExport ? { images: { unoptimized: true } } : { async rewrites() {
    return {
      afterFiles: [
        {
          source: "/api/market/:path*",
          destination: `${process.env.MARKET_API_BASE_URL ?? "http://127.0.0.1:8210"}/api/market/:path*`
        }
      ]
    };
  } })
};

export default nextConfig;

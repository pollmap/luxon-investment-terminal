import type { Metadata } from "next";
import "./styles.css";

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ??
  (process.env.VERCEL_PROJECT_PRODUCTION_URL
    ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
    : "http://localhost:3100");

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: "LUXON Investment Terminal",
  description: "Source-traced fundamental valuation, forecast, and data audit terminal",
  icons: {
    icon: "/icon.svg",
    shortcut: "/icon.svg",
    apple: "/valuetrace-mark.png"
  },
  openGraph: {
    title: "LUXON Investment Terminal",
    description: "Source-traced fundamental valuation, forecast, and data audit terminal",
    images: [
      {
        url: "/valuetrace-og.png",
        width: 1200,
        height: 630,
        alt: "LUXON source-traced valuation terminal preview"
      }
    ]
  },
  twitter: {
    card: "summary_large_image",
    title: "LUXON Investment Terminal",
    description: "Source-traced fundamental valuation, forecast, and data audit terminal",
    images: ["/valuetrace-og.png"]
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import "./styles.css";

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ??
  (process.env.VERCEL_PROJECT_PRODUCTION_URL
    ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
    : "http://localhost:3100");

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: "주식담 — 국내 주식 시세와 기업 정보",
  description: "국내 상장종목의 시세, 순위, 차트와 기업 정보를 살펴보는 주식 대시보드.",
  icons: {
    icon: "/brand/jusikdam-symbol.png",
    shortcut: "/brand/jusikdam-symbol.png",
    apple: "/brand/jusikdam-symbol.png"
  },
  openGraph: {
    title: "주식담 — 국내 주식 시세와 기업 정보",
    description: "국내 상장종목의 시세, 순위, 차트와 기업 정보를 살펴보는 주식 대시보드."
  },
  twitter: {
    card: "summary_large_image",
    title: "주식담 — 국내 주식 시세와 기업 정보",
    description: "국내 상장종목의 시세, 순위, 차트와 기업 정보를 살펴보는 주식 대시보드."
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}

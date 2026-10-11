import type { Metadata } from 'next';
import Dashboard from '../../components/market/dashboard';
import './market.css';
import './workspace.css';

export const metadata: Metadata = {
  title: '주식 장보기 — 시세와 기업 정보',
  description: '시장 흐름과 국내 상장기업 정보를 쉽고 빠르게 찾아보는 주식 대시보드.',
  manifest: '/stocks/manifest.webmanifest',
};

export default function StocksPage() { return <Dashboard />; }

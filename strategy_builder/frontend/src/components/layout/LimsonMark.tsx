/**
 * LIMSON 브랜드 마크 (남색 라운드 사각형 + 흰색 L 축 + 초록 상승 라인)
 */
export function LimsonMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 100 100" className={className} role="img" aria-label="LIMSON">
      <rect width="100" height="100" rx="19" fill="#121d33" />
      <path d="M29 22 V75.5 H79.5" fill="none" stroke="#ffffff" strokeWidth="9.4" strokeLinecap="round" strokeLinejoin="round" />
      <polyline
        points="41,62.7 53,48.8 61.6,55.5 74.4,36"
        fill="none"
        stroke="#34e3a1"
        strokeWidth="6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="74.6" cy="35.3" r="6.2" fill="#34e3a1" />
    </svg>
  );
}

export default LimsonMark;

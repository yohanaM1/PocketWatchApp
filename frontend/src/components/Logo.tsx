/** The PocketWatch mark: a watch dial with a crown, ticks at 12/3/6/9, and hands. Decorative. */
export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect x="14" y="1.5" width="4" height="3" rx="1" fill="var(--brass)" />
      <rect x="15.2" y="4" width="1.6" height="3" fill="var(--brass)" />
      <circle cx="16" cy="18.5" r="11.5" fill="var(--dial)" stroke="var(--brass)" strokeWidth="2.5" />
      <g stroke="var(--dial-ink)" strokeWidth="1.6" strokeLinecap="round">
        <line x1="16" y1="9.5" x2="16" y2="11.5" />
        <line x1="25" y1="18.5" x2="23" y2="18.5" />
        <line x1="16" y1="27.5" x2="16" y2="25.5" />
        <line x1="7" y1="18.5" x2="9" y2="18.5" />
        <line x1="16" y1="18.5" x2="16" y2="12.5" strokeWidth="1.8" />
        <line x1="16" y1="18.5" x2="20.5" y2="21" stroke="var(--brass)" strokeWidth="1.8" />
      </g>
      <circle cx="16" cy="18.5" r="1.4" fill="var(--brass)" />
    </svg>
  );
}

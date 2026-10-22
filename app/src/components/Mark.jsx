// The Post House mark: a house with a play button inside. SoldFast is real estate; this is the post-production house for it. Used once in the title bar and on the home screen.
export default function Mark({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="ph-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#0391d8" />
          <stop offset="1" stopColor="#033459" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="7.5" fill="url(#ph-mark)" />
      <path d="M7.5 15.2L16 8l8.5 7.2V24.5h-17z" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M13.8 16.2v5l4.4-2.5z" fill="#fff" />
    </svg>
  );
}

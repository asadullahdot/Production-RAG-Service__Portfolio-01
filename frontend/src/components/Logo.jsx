// The KNOWLEX mark: a "K" with a small spark, on a soft gradient. Pass a size class, for example "size-9".
export default function Logo({ className = "size-9" }) {
  return (
    <span aria-hidden="true"
      className={`grid shrink-0 place-items-center rounded-[32%] bg-linear-to-br from-indigo-600 to-violet-600 shadow-lg shadow-indigo-500/40 dark:from-indigo-500 dark:to-violet-500 ${className}`}>
      <svg viewBox="0 0 24 24" className="size-[62%]" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M7 4v16M17 5.5 7 12.5l10 6.5" />
        <circle cx="18.2" cy="5" r="1.7" fill="#fde68a" stroke="none" />
      </svg>
    </span>
  );
}

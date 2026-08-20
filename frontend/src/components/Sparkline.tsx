/**
 * Inline trend for a repeated measurement. Deliberately unlabelled and
 * unaxised: it is a shape cue for "is this stable, climbing, falling", read in
 * passing next to the numbers that are already on screen. It is never the
 * thing a decision is made from, so it carries no gridlines, no scale and no
 * interaction — the exact readings sit beside it.
 *
 * `points` arrive oldest-first.
 */
export function Sparkline({
  points,
  label,
  width = 68,
  height = 18,
}: {
  points: number[];
  label: string;
  width?: number;
  height?: number;
}) {
  if (points.length < 2) return null;

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min;
  const step = width / (points.length - 1);

  // A flat series would divide by zero; draw it down the middle instead, which
  // is also the honest picture of "this has not moved".
  const y = (value: number) =>
    span === 0 ? height / 2 : height - ((value - min) / span) * (height - 2) - 1;

  const d = points.map((value, i) => `${i === 0 ? "M" : "L"}${i * step},${y(value)}`).join(" ");
  const lastX = (points.length - 1) * step;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="overflow-visible text-ink-faint"
      role="img"
      aria-label={`${label}: ${points.length} readings, ${points[0]} to ${points[points.length - 1]}`}
    >
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round" />
      <circle cx={lastX} cy={y(points[points.length - 1])} r="1.9" className="fill-accent" />
    </svg>
  );
}

/** A color pair from a name, so a project with no footage still has its own look (always from the brand's blues and navy). Pure, so it can be tested on its own. */
export function tileColors(name) {
  let h = 0;
  for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const hues = [[3, 52, 89], [3, 74, 120], [6, 86, 130], [8, 60, 104], [4, 66, 96]];
  const a = hues[h % hues.length], b = hues[(h >>> 3) % hues.length];
  return [`rgb(${a[0]}, ${a[1]}, ${a[2]})`, `rgb(${Math.min(255, b[0] + 12)}, ${Math.min(255, b[1] + 40)}, ${Math.min(255, b[2] + 70)})`];
}

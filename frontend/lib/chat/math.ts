// Fenced code blocks (``` or ~~~) and inline code spans: math delimiters inside stay as-is.
const CODE = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/g;

/**
 * Rewrites LaTeX's `\[ … \]` and `\( … \)` delimiters as `$$ … $$` and `$ … $`, the only
 * forms remark-math understands. Models use both styles.
 */
export function normalizeMathDelimiters(markdown: string): string {
  return markdown
    .split(CODE)
    .map((segment, index) => {
      // split() with a capture group puts the code matches at odd indexes.
      if (index % 2 === 1) return segment;
      return segment
        .replace(/\\\[([\s\S]*?)\\\]/g, (_, math: string) => `\n$$\n${math.trim()}\n$$\n`)
        .replace(/\\\(([\s\S]*?)\\\)/g, (_, math: string) => `$${math.trim()}$`);
    })
    .join("");
}

import { marked } from "marked";
import DOMPurify from "dompurify";

// Wrap rendered <table> elements so wide tables scroll inside the bubble
// instead of overflowing the page, matching the plain-JS version's behaviour.
function wrapTables(html: string): string {
  return html.replace(/<table[\s\S]*?<\/table>/g, (table) => `<div class="table-scroll">${table}</div>`);
}

export function renderMarkdown(text: string): string {
  const html = DOMPurify.sanitize(marked.parse(text, { breaks: true, async: false }) as string);
  return wrapTables(html);
}

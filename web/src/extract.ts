// Text extraction in the browser, so the Worker never has to parse files.
// PDFs: pdf.js text items are regrouped into lines by their vertical position.
// HTML: visible text. PDFs, images and Office files can also be read from their saved originals.

export type Extracted = { text: string; pages?: number };

export type TextItem = { str: string; x: number; y: number };

/** Group positioned text items into lines (top to bottom, left to right). */
export function itemsToLines(items: TextItem[], tolerance = 2): string {
  const rows: { y: number; items: TextItem[] }[] = [];
  for (const it of items) {
    if (!it.str.trim()) continue;
    const row = rows.find((r) => Math.abs(r.y - it.y) <= tolerance);
    if (row) row.items.push(it);
    else rows.push({ y: it.y, items: [it] });
  }
  rows.sort((a, b) => b.y - a.y); // PDF y grows upward
  return rows
    .map((r) => r.items.sort((a, b) => a.x - b.x).map((i) => i.str.trim()).join(" "))
    .join("\n");
}

export function htmlToText(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.querySelectorAll("script,style,noscript").forEach((n) => n.remove());
  // keep table rows and blocks on their own lines
  doc.querySelectorAll("tr,p,div,li,br,h1,h2,h3,h4").forEach((n) => n.append("\n"));
  doc.querySelectorAll("td,th").forEach((n) => n.append(" "));
  return (doc.body.textContent || "").replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n").trim();
}

export async function extractText(file: File): Promise<Extracted> {
  const name = file.name.toLowerCase();
  const signature = await file.slice(0, 5).text();
  if (file.type === "application/pdf" || name.endsWith(".pdf") || signature === "%PDF-") {
    const pdfjs = await import("pdfjs-dist");
    const workerUrl = (await import("pdfjs-dist/build/pdf.worker.min.mjs?url")).default;
    pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
    const pdf = await pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) }).promise;
    const pages: string[] = [];
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const content = await page.getTextContent();
      const items: TextItem[] = [];
      for (const it of content.items) {
        if ("str" in it) items.push({ str: it.str, x: it.transform[4], y: it.transform[5] });
      }
      pages.push(itemsToLines(items));
    }
    return { text: pages.join("\n"), pages: pdf.numPages };
  }
  if (file.type.startsWith("image/") || /\.(docx|xlsx|png|jpe?g|webp|tiff?)$/i.test(name)) return { text: "" };
  const raw = await file.text();
  if (file.type === "text/html" || name.endsWith(".html") || name.endsWith(".htm")) return { text: htmlToText(raw) };
  return { text: raw };
}

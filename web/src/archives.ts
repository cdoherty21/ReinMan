export const MAX_FILE_BYTES = 8 * 1024 * 1024;
const MAX_ARCHIVE_BYTES = 64 * 1024 * 1024;
const MAX_EXPANDED_BYTES = 128 * 1024 * 1024;
const MAX_ARCHIVE_ENTRIES = 250;
const DOCUMENT_EXT = /\.(pdf|html?|txt|csv|json)$/i;

export async function* expandArchive(archive: File): AsyncGenerator<File | string> {
  if (archive.size > MAX_ARCHIVE_BYTES) throw new Error("ZIP exceeds 64 MB.");
  const { BlobReader, ZipReader } = await import("@zip.js/zip.js");
  const reader = new ZipReader(new BlobReader(archive), { useWebWorkers: false });
  let entries = 0;
  let expanded = 0;
  try {
    for await (const entry of reader.getEntriesGenerator()) {
      if (++entries > MAX_ARCHIVE_ENTRIES) throw new Error("ZIP exceeds 250 entries.");
      const path = entry.filename.replace(/\\/g, "/").split("/");
      const name = path.at(-1) ?? "";
      if (entry.directory || path.some((part) => part.startsWith(".") || part === "__MACOSX")
        || /^(README|MANIFEST)\.(txt|csv)$/i.test(name) || !DOCUMENT_EXT.test(name)) continue;
      try {
        if (entry.encrypted) throw new Error("Password-protected file.");
        if (entry.uncompressedSize > MAX_FILE_BYTES) throw new Error("File exceeds 8 MB.");
        const parts: BlobPart[] = [];
        let bytes = 0;
        await entry.getData(new WritableStream<Uint8Array>({
          write(chunk) {
            bytes += chunk.byteLength;
            expanded += chunk.byteLength;
            if (bytes > MAX_FILE_BYTES) throw new Error("File exceeds 8 MB.");
            if (expanded > MAX_EXPANDED_BYTES) throw new Error("ZIP exceeds 128 MB of extracted documents.");
            parts.push(new Uint8Array(chunk));
          },
        }), { checkSignature: true });
        yield new File(parts, name);
      } catch (error) {
        yield `${name} — ${(error as Error).message}`;
        if (expanded > MAX_EXPANDED_BYTES) return;
      }
    }
  } finally {
    await reader.close();
  }
}

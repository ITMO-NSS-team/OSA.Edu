declare module "pdf-parse" {
  interface PdfResult { text: string; numpages?: number; info?: Record<string, unknown>; metadata?: unknown; version?: string; }
  function pdf(dataBuffer: Buffer): Promise<PdfResult>;
  export default pdf;
}

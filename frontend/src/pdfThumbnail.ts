import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
// Vite bundle le worker comme asset séparé et nous donne son URL finale —
// nécessaire car pdf.js exécute le parsing dans un Web Worker, jamais sur
// le thread principal (fichiers potentiellement volumineux).
import pdfjsWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = pdfjsWorkerUrl;

const THUMBNAIL_WIDTH_PX = 96;

/**
 * Rend la première page d'un PDF en miniature PNG (data URL), pour un
 * aperçu "sans y entrer" dans les chips de fichiers joints (cf. App.tsx).
 * Accepte soit un File local (avant envoi), soit une URL (pièce jointe déjà
 * envoyée, servie par GET /api/sessions/{id}/attachments/{id}/file).
 *
 * Ne lève jamais silencieusement en boucle infinie côté appelant : les
 * erreurs (PDF corrompu, format non standard) remontent normalement pour
 * que l'appelant retombe sur le badge d'extension plutôt que sur du texte
 * cassé.
 */
export async function renderPdfThumbnail(source: File | string): Promise<string> {
  const loadingTask =
    typeof source === "string"
      ? getDocument({ url: source, withCredentials: true })
      : getDocument({ data: await source.arrayBuffer() });

  const pdf = await loadingTask.promise;
  try {
    const page = await pdf.getPage(1);
    const baseViewport = page.getViewport({ scale: 1 });
    const scale = THUMBNAIL_WIDTH_PX / baseViewport.width;
    const viewport = page.getViewport({ scale });

    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;

    await page.render({ canvas, viewport }).promise;
    return canvas.toDataURL("image/png");
  } finally {
    await loadingTask.destroy();
  }
}

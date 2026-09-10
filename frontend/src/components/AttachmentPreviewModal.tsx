import { useEffect } from "react";

interface AttachmentPreviewModalProps {
  filename: string;
  url: string;
  contentType: string;
  onClose: () => void;
}

/**
 * Aperçu "dans l'interface" (jamais un nouvel onglet navigateur) : image ou
 * PDF rendus directement via le lecteur natif du navigateur (<img>/<iframe>,
 * aucune librairie externe nécessaire). Les formats sans rendu natif
 * possible (docx/xlsx/pptx) retombent sur un lien de téléchargement — pas
 * de faux positif "aperçu" pour un format qu'on ne peut pas vraiment afficher.
 */
export function AttachmentPreviewModal({ filename, url, contentType, onClose }: AttachmentPreviewModalProps) {
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  const isImage = contentType.startsWith("image/");
  const isPdf = contentType === "application/pdf";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Aperçu de ${filename}`}
    >
      <div
        className="flex h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900 shadow-glow"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-slate-800 px-4 py-3">
          <p className="truncate text-sm font-medium text-slate-200">{filename}</p>
          <button
            type="button"
            onClick={onClose}
            aria-label="Fermer l'aperçu"
            className="shrink-0 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-auto bg-black/40">
          {isImage && (
            <img src={url} alt={filename} className="mx-auto max-h-full max-w-full object-contain" />
          )}
          {isPdf && <iframe src={url} title={filename} className="h-full w-full" />}
          {!isImage && !isPdf && (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
              <p className="text-sm text-slate-400">
                Aperçu non disponible dans le navigateur pour ce type de fichier.
              </p>
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800"
              >
                Ouvrir le fichier
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

import { downloadUrl as buildUrl } from "../state/api";
import type { DocType } from "../state/types";

const EXT: Record<DocType, string> = { odp: ".odp", odt: ".odt", ods: ".ods" };

export function DownloadDock({ jobId, downloadUrl, docType }: { jobId?: string; downloadUrl?: string; docType: DocType }) {
  if (downloadUrl && jobId) {
    return <a className="dl done" href={buildUrl(jobId)} download>下載 {EXT[docType]} <span className="odp">ODF</span></a>;
  }
  return <button className="dl" disabled>生成中… <span className="odp">ODF</span></button>;
}

import { downloadUrl as buildUrl } from "../state/api";
import type { DocType, Phase } from "../state/types";

const EXT: Record<DocType, string> = { odp: ".odp", odt: ".odt", ods: ".ods" };

export function DownloadDock({
  jobId, downloadUrl, docType, phase,
}: {
  jobId?: string;
  downloadUrl?: string;
  docType: DocType;
  phase?: Phase;
}) {
  if (downloadUrl && jobId) {
    return <a className="dl done" href={buildUrl(jobId)} download>下載 {EXT[docType]} <span className="odp">ODF</span></a>;
  }
  // 空台:還沒開始生成,不擺一顆「生成中…」的假動作按鈕(空台矛盾)。
  if (phase === "empty") return null;
  // 生成中/其他未就緒:誠實標「尚未生成」,明確 disabled。
  return <button className="dl" disabled>尚未生成 <span className="odp">ODF</span></button>;
}

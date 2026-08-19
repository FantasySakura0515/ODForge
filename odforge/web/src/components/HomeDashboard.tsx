import { useState } from "react";
import type { SessionSummary } from "../state/api";

const STATUS_LABELS: Record<string, string> = {
  pending: "準備中",
  generating_outline: "建立大綱",
  awaiting_approval: "待確認",
  generating_slides: "生成中",
  rendering: "排版中",
  qa: "檢查中",
  complete: "已完成",
  error: "中斷",
  cancelled: "已取消",
};

function formatTime(timestamp: number): string {
  return new Intl.DateTimeFormat("zh-TW", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp * 1000));
}

export function HomeDashboard({
  sessions,
  loading,
  error,
  onNew,
  onReload,
  onDelete,
}: {
  sessions: SessionSummary[];
  loading: boolean;
  error: string;
  onNew: () => void;
  onReload: () => void;
  onDelete: (id: string) => Promise<void>;
}) {
  // 刪除是兩步的:第一次點只是把確認列展開。這裡刪掉的是伺服器上唯一的一份
  // .odp——使用者可能還沒下載過它,而清單上每一列長得一模一樣,點錯一列的代價
  // 不該是「東西沒了」。同時只允許一列處於確認狀態,免得畫面上出現兩個都寫著
  // 「確定刪除」的按鈕。
  const [confirming, setConfirming] = useState("");
  const [deleting, setDeleting] = useState("");
  const [deleteError, setDeleteError] = useState("");

  async function confirmDelete(session: SessionSummary) {
    setDeleting(session.id);
    setDeleteError("");
    try {
      await onDelete(session.id);
      setConfirming("");
    } catch (exc) {
      // 後端會說明為什麼不能刪(例如工作還在跑),那句話比「刪除失敗」有用。
      setDeleteError(exc instanceof Error ? exc.message : "刪除失敗，請稍後再試。");
    } finally {
      setDeleting("");
    }
  }

  return (
    <main className="home-shell" id="main-content">
      <section className="home-head" aria-labelledby="home-title">
        <div>
          <span className="home-kicker">簡報工作台</span>
          <h1 id="home-title">最近工作</h1>
        </div>
      </section>

      <section className="session-archive" aria-labelledby="session-title">
        <header className="archive-head">
          <div>
            <h2 id="session-title">Session</h2>
            <span>{sessions.length ? `${sessions.length} 份紀錄` : "工作紀錄"}</span>
          </div>
          {!loading && (
            <button type="button" className="archive-refresh" onClick={onReload}>
              重新整理
            </button>
          )}
        </header>

        {loading ? (
          <div className="archive-state" aria-live="polite">
            <span className="archive-loader" aria-hidden="true" />
            <p>正在讀取工作紀錄…</p>
          </div>
        ) : error ? (
          <div className="archive-state archive-error" role="alert">
            <p>{error}</p>
            <button type="button" className="text-action" onClick={onReload}>重試</button>
          </div>
        ) : sessions.length === 0 ? (
          <div className="archive-state archive-empty">
            <span aria-hidden="true">00</span>
            <h2>還沒有簡報</h2>
            <p>建立第一份後，會保留在這裡。</p>
            <button type="button" className="text-action" onClick={onNew}>
              建立第一份簡報
            </button>
          </div>
        ) : (
          <div className="session-list">
            {sessions.map((session, index) => (
              <article className="session-card" data-status={session.status} key={session.id}>
                <a className="session-main" href={`?job=${encodeURIComponent(session.id)}`}>
                  <span className="session-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="session-thumb">
                    {session.preview_url ? (
                      <img src={session.preview_url} alt="" loading="lazy" />
                    ) : (
                      <span aria-hidden="true">{session.status === "complete" ? "ODP" : "…"}</span>
                    )}
                  </span>
                  <span className="session-copy">
                    <span className="session-meta">
                      <span className="session-status">{STATUS_LABELS[session.status] ?? session.status}</span>
                      <time dateTime={new Date(session.updated_at * 1000).toISOString()}>
                        {formatTime(session.updated_at)}
                      </time>
                    </span>
                    <strong>{session.title}</strong>
                    <small>{session.prompt}</small>
                  </span>
                  <span className="session-pages">
                    <b>{session.page_count || "—"}</b>
                    <small>頁</small>
                  </span>
                  <span className="session-arrow" aria-hidden="true">↗</span>
                </a>
                {session.download_url && (
                  <a
                    className="session-download"
                    href={session.download_url}
                    download
                    aria-label={`下載 ${session.title}`}
                  >
                    下載
                  </a>
                )}
                <button
                  type="button"
                  className="session-delete"
                  aria-label={`刪除 ${session.title}`}
                  aria-expanded={confirming === session.id}
                  onClick={() => {
                    setConfirming(confirming === session.id ? "" : session.id);
                    setDeleteError("");
                  }}
                >
                  刪除
                </button>
                {confirming === session.id && (
                  <div
                    className="session-confirm"
                    role="group"
                    aria-label={`確認刪除 ${session.title}`}
                  >
                    <p>刪除後，伺服器上的 .odp 與預覽圖都會一併移除，無法復原。</p>
                    <span className="session-confirm-actions">
                      <button
                        type="button"
                        className="text-action danger"
                        disabled={deleting === session.id}
                        onClick={() => void confirmDelete(session)}
                      >
                        {deleting === session.id ? "刪除中…" : "確定刪除"}
                      </button>
                      <button
                        type="button"
                        className="text-action"
                        onClick={() => setConfirming("")}
                      >
                        保留
                      </button>
                    </span>
                    {deleteError && (
                      <p className="session-confirm-error" role="alert">{deleteError}</p>
                    )}
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

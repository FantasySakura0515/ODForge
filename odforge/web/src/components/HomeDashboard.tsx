import { TemplateGallery } from "./TemplateGallery";
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
}: {
  sessions: SessionSummary[];
  loading: boolean;
  error: string;
  onNew: () => void;
  onReload: () => void;
}) {
  return (
    <main className="home-shell" id="main-content">
      <section className="home-head" aria-labelledby="home-title">
        <div>
          <span className="home-kicker">簡報工作台</span>
          <h1 id="home-title">最近工作</h1>
        </div>
        <button type="button" className="new-session" onClick={onNew}>
          <span className="new-session-mark" aria-hidden="true">＋</span>
          <span>
            <b>新增簡報</b>
            <small>從需求或參考文件開始</small>
          </span>
          <i aria-hidden="true">→</i>
        </button>
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
            <button type="button" className="text-action" onClick={onNew}>新增簡報</button>
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
              </article>
            ))}
          </div>
        )}
      </section>

      <TemplateGallery />
    </main>
  );
}

import { TemplateGallery } from "./TemplateGallery";

/** 範本庫自己的一頁。首頁只留一個入口,不再把整座畫廊掛在工作紀錄底下。 */
export function TemplatesPage({ onBack }: { onBack: () => void }) {
  return (
    <main className="home-shell" id="main-content">
      <section className="home-head" aria-labelledby="templates-title">
        <div>
          <span className="home-kicker">設計</span>
          <h1 id="templates-title">範本庫</h1>
        </div>
        <button type="button" className="archive-refresh" onClick={onBack}>
          ← 回到首頁
        </button>
      </section>

      <TemplateGallery />
    </main>
  );
}

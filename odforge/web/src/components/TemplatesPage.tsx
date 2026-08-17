import { TemplateGallery } from "./TemplateGallery";

/** 範本庫自己的一頁。導覽在左側常駐的 SideNav,這裡只放內容。 */
export function TemplatesPage() {
  return (
    <main className="home-shell" id="main-content">
      <section className="home-head" aria-labelledby="templates-title">
        <div>
          <span className="home-kicker">設計</span>
          <h1 id="templates-title">範本庫</h1>
        </div>
      </section>

      <TemplateGallery />
    </main>
  );
}

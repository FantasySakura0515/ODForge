/**
 * 左側常駐導覽。首頁與範本庫是兩個並列的地方,不是「首頁往下捲會遇到的東西」——
 * 導覽固定在左邊,兩頁共用同一條,所以在哪一頁都看得到自己在哪。
 */
export type NavView = "home" | "templates";

const ITEMS: { id: NavView; label: string; hint: string; mark: string }[] = [
  { id: "home", label: "工作紀錄", hint: "最近生成的簡報", mark: "▤" },
  { id: "templates", label: "範本庫", hint: "版式與配色", mark: "◧" },
];

export function SideNav({
  current,
  onNavigate,
  onNew,
}: {
  current: NavView;
  onNavigate: (view: NavView) => void;
  onNew: () => void;
}) {
  return (
    <nav className="sidenav" aria-label="主要導覽">
      <button type="button" className="sidenav-new" onClick={onNew}>
        <span aria-hidden="true">＋</span>
        <span>新增簡報</span>
      </button>

      <ul>
        {ITEMS.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              className={current === item.id ? "sidenav-item on" : "sidenav-item"}
              aria-current={current === item.id ? "page" : undefined}
              onClick={() => onNavigate(item.id)}
            >
              <span className="sidenav-mark" aria-hidden="true">{item.mark}</span>
              <span>
                <b>{item.label}</b>
                <small>{item.hint}</small>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

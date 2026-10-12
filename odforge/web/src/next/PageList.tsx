import { useState } from "react";
import { SlideView } from "./SlideView";
import type { Scene } from "./types";

const THUMB_WIDTH = 168;

export function PageList({
  scene,
  current,
  onSelect,
  onAdd,
  onDuplicate,
  onDelete,
  onMove,
}: {
  scene: Scene;
  current: number;
  onSelect: (index: number) => void;
  onAdd: () => void;
  onDuplicate: (index: number) => void;
  onDelete: (index: number) => void;
  onMove: (from: number, to: number) => void;
}) {
  const s = THUMB_WIDTH / scene.width;
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);

  function drop() {
    if (dragFrom !== null && dropAt !== null) {
      const to = dropAt > dragFrom ? dropAt - 1 : dropAt;
      if (to !== dragFrom) onMove(dragFrom, to);
    }
    setDragFrom(null);
    setDropAt(null);
  }

  return (
    <nav className="ne-pages" aria-label="頁面">
      <ol>
        {scene.pages.map((page, i) => (
          <li
            key={`${page.name}-${i}`}
            className={[
              "ne-page",
              i === current ? "current" : "",
              dropAt === i && dragFrom !== null ? "drop-before" : "",
              dropAt === i + 1 && i === scene.pages.length - 1 && dragFrom !== null ? "drop-after" : "",
            ].join(" ")}
            draggable
            onDragStart={(e) => {
              setDragFrom(i);
              e.dataTransfer.effectAllowed = "move";
            }}
            onDragOver={(e) => {
              e.preventDefault();
              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
              setDropAt(e.clientY < rect.top + rect.height / 2 ? i : i + 1);
            }}
            onDrop={(e) => {
              e.preventDefault();
              drop();
            }}
            onDragEnd={() => {
              setDragFrom(null);
              setDropAt(null);
            }}
          >
            <span className="ne-page-no">{i + 1}</span>
            <button
              type="button"
              className="ne-thumb"
              aria-label={`第 ${i + 1} 頁`}
              aria-current={i === current ? "page" : undefined}
              onClick={() => onSelect(i)}
            >
              <SlideView page={page} width={scene.width} height={scene.height} s={s} />
            </button>
            <span className="ne-page-actions">
              <button type="button" aria-label={`複製第 ${i + 1} 頁`} title="複製這一頁" onClick={() => onDuplicate(i)}>
                ⧉
              </button>
              <button
                type="button"
                aria-label={`刪除第 ${i + 1} 頁`}
                title="刪除這一頁"
                disabled={scene.pages.length <= 1}
                onClick={() => onDelete(i)}
              >
                ×
              </button>
            </span>
          </li>
        ))}
      </ol>
      <button type="button" className="ne-add-page" onClick={onAdd}>
        ＋ 新增頁
      </button>
    </nav>
  );
}
